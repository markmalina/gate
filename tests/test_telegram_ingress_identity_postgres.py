"""PostgreSQL guards for coherent, short-lived Telegram identity reads."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.telegram_ingress_identity import TelegramIngressIdentitySnapshot
from app.application.telegram_processing import AcceptTelegramReceiptCommand
from app.core.config import Settings, get_settings
from app.infrastructure.telegram_ingress_identity import (
    SessionFactoryTelegramIngressIdentityReader,
)
from app.infrastructure.telegram_processing import SqlTelegramReceiptStore
from app.models.domain import (
    Account,
    AuditLog,
    Base,
    Conversation,
    GateEvent,
    Message,
    ReplyDraft,
    ReplyTask,
    SendAttempt,
    TelegramBusinessConnection,
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
)
from app.worker.telegram_processing import run_telegram_worker_once
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _account(*, alias: str, owner: str, telegram_user_id: int, active: bool = True) -> Account:
    return Account(
        alias=alias,
        owner=owner,
        telegram_user_id=telegram_user_id,
        account_type="personal",
        permissions={},
        is_active=active,
    )


def _receipt_command(
    *,
    update_id: int,
    connection_id: str,
    received_at: datetime,
) -> AcceptTelegramReceiptCommand:
    return AcceptTelegramReceiptCommand(
        bot_key="gate_m_bot",
        update_id=update_id,
        payload={
            "update_id": update_id,
            "business_message": {
                "message_id": update_id,
                "business_connection_id": connection_id,
                "chat": {"id": 710000001, "type": "private"},
                "from": {"id": 720000001, "is_bot": False},
                "text": "Око возрождения",
            },
        },
        received_at=received_at,
    )


class SqlFailingIdentityReader:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        async with self._sessions() as session:
            await session.execute(text("SELECT * FROM synthetic_missing_identity_table"))
        return None


@pytest.mark.anyio
async def test_identity_snapshot_is_coherent_and_releases_single_pool_slot() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL identity test")

    schema = f"identity_test_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
        pool_size=1,
        max_overflow=0,
    )
    capture_identity_sql = False
    identity_sql: list[str] = []

    def capture_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if capture_identity_sql:
            identity_sql.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture_sql)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        target = _account(alias="identity_target", owner="same-owner", telegram_user_id=101)
        sibling = _account(alias="identity_sibling", owner="same-owner", telegram_user_id=202)
        inactive = _account(
            alias="identity_inactive",
            owner="same-owner",
            telegram_user_id=303,
            active=False,
        )
        empty_owner = _account(
            alias="identity_empty_owner",
            owner="   ",
            telegram_user_id=304,
        )
        control_only_owner = _account(
            alias="identity_control_only_owner",
            owner="\t\n",
            telegram_user_id=305,
        )
        async with sessions() as seed:
            seed.add_all([target, sibling, inactive, empty_owner, control_only_owner])
            await seed.flush()
            seed.add_all(
                [
                    TelegramBusinessConnection(
                        account_id=target.id,
                        business_connection_id="identity-enabled",
                        telegram_user_id=404,
                        user_chat_id=505,
                        rights={},
                        is_enabled=True,
                    ),
                    TelegramBusinessConnection(
                        account_id=target.id,
                        business_connection_id="identity-target-historical",
                        telegram_user_id=414,
                        user_chat_id=515,
                        rights={},
                        is_enabled=False,
                    ),
                    TelegramBusinessConnection(
                        account_id=sibling.id,
                        business_connection_id="identity-historical",
                        telegram_user_id=606,
                        user_chat_id=707,
                        rights={},
                        is_enabled=False,
                    ),
                    TelegramBusinessConnection(
                        account_id=inactive.id,
                        business_connection_id="identity-inactive-account",
                        telegram_user_id=808,
                        user_chat_id=909,
                        rights={},
                        is_enabled=True,
                    ),
                    TelegramBusinessConnection(
                        account_id=empty_owner.id,
                        business_connection_id="identity-empty-owner",
                        telegram_user_id=810,
                        user_chat_id=910,
                        rights={},
                        is_enabled=True,
                    ),
                    TelegramBusinessConnection(
                        account_id=control_only_owner.id,
                        business_connection_id="identity-control-only-owner",
                        telegram_user_id=811,
                        user_chat_id=911,
                        rights={},
                        is_enabled=True,
                    ),
                ]
            )
            await seed.commit()

        reader = SessionFactoryTelegramIngressIdentityReader(sessions)
        async with sessions() as queue_session:
            await queue_session.execute(text("SELECT 1"))
            await queue_session.commit()
            capture_identity_sql = True
            snapshot = await asyncio.wait_for(
                reader.read(business_connection_id="identity-enabled"),
                timeout=5,
            )
            capture_identity_sql = False
            assert snapshot is not None
            assert snapshot.account_alias == "identity_target"
            assert snapshot.owner_identity_ids == frozenset(
                {"101", "404", "505", "414", "515"}
            )
            assert snapshot.owner_identity_ids.isdisjoint(
                {"202", "606", "707"}
            )
            assert len(identity_sql) == 1
            normalized_sql = identity_sql[0].upper()
            assert normalized_sql.lstrip().startswith("SELECT")
            assert " FOR UPDATE" not in normalized_sql
            assert await asyncio.wait_for(queue_session.scalar(text("SELECT 1")), timeout=5) == 1

        assert await reader.read(business_connection_id="identity-historical") is None
        assert await reader.read(business_connection_id="identity-inactive-account") is None
        assert await reader.read(business_connection_id="identity-empty-owner") is None
        assert await reader.read(business_connection_id="identity-control-only-owner") is None
        assert await reader.read(business_connection_id="identity-unknown") is None
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.anyio
async def test_single_pool_slot_completes_success_and_persists_identity_sql_retry() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL identity lifecycle test")

    schema = f"identity_worker_test_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
        pool_size=1,
        max_overflow=0,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        account = _account(
            alias="telegram_mark_ru",
            owner="worker-owner",
            telegram_user_id=730000001,
        )
        async with sessions() as seed:
            seed.add(account)
            await seed.flush()
            seed.add(
                TelegramBusinessConnection(
                    account_id=account.id,
                    business_connection_id="worker-success-connection",
                    telegram_user_id=730000001,
                    user_chat_id=740000001,
                    rights={},
                    is_enabled=True,
                )
            )
            await seed.commit()
            store = SqlTelegramReceiptStore(seed)
            await store.create_if_absent(
                _receipt_command(
                    update_id=301,
                    connection_id="worker-success-connection",
                    received_at=now,
                )
            )
            await store.create_if_absent(
                _receipt_command(
                    update_id=302,
                    connection_id="worker-failing-connection",
                    received_at=now + timedelta(seconds=1),
                )
            )

        settings = Settings(_env_file=None, GATE_TELEGRAM_WORKER_ENABLED=True)
        async with sessions() as queue_session:
            processed = await asyncio.wait_for(
                run_telegram_worker_once(
                    settings=settings,
                    session=queue_session,
                    identity_reader=SessionFactoryTelegramIngressIdentityReader(sessions),
                    now=now + timedelta(seconds=2),
                ),
                timeout=8,
            )
            retried = await asyncio.wait_for(
                run_telegram_worker_once(
                    settings=settings,
                    session=queue_session,
                    identity_reader=SqlFailingIdentityReader(sessions),
                    now=now + timedelta(seconds=2),
                ),
                timeout=8,
            )

        assert processed.disposition == "processed"
        assert retried.disposition == "retry"
        assert retried.error_code == "identity_snapshot_failed"
        async with sessions() as verify:
            receipts = (
                await verify.execute(select(TelegramReceipt).order_by(TelegramReceipt.update_id))
            ).scalars().all()
            events = (
                await verify.execute(
                    select(TelegramOutboxEvent).order_by(TelegramOutboxEvent.available_at)
                )
            ).scalars().all()
            workflow_counts = {
                model.__tablename__: await verify.scalar(
                    select(func.count()).select_from(model)
                )
                for model in (
                    Message,
                    GateEvent,
                    Conversation,
                    ReplyTask,
                    ReplyDraft,
                    SendAttempt,
                    AuditLog,
                )
            }

        assert [receipt.status for receipt in receipts] == [
            TelegramReceiptStatus.PROCESSED.value,
            TelegramReceiptStatus.RECEIVED.value,
        ]
        assert {event.status for event in events} == {
            TelegramOutboxStatus.DELIVERED.value,
            TelegramOutboxStatus.RETRY_SCHEDULED.value,
        }
        assert all(event.status != TelegramOutboxStatus.PROCESSING.value for event in events)
        assert workflow_counts == {
            "messages": 0,
            "gate_events": 0,
            "conversations": 0,
            "reply_tasks": 0,
            "reply_drafts": 0,
            "send_attempts": 0,
            "audit_logs": 0,
        }
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
