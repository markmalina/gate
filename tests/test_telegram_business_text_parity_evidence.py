"""PostgreSQL and schema guards for content-free Business text parity evidence."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    WorkRoute,
)
from app.application.telegram_business_text_parity import (
    TelegramBusinessTextParityCommand,
    TelegramBusinessTextParityState,
)
from app.core.config import get_settings
from app.infrastructure.telegram_business_text_parity import (
    TELEGRAM_BUSINESS_TEXT_PARITY_DIRECTION_POLICY_VERSION,
    _record_evidence,
    _status,
    telegram_business_text_parity_status,
)
from app.models.agent import AgentTurnModel
from app.models.canonical import (
    CanonicalMessage,
    Dialog,
    TelegramBusinessTextParityEvidence,
    TransportConnector,
    Workspace,
)
from app.models.domain import (
    Account,
    Base,
    ReplyDraft,
    ReplyTask,
    SendAttempt,
    TelegramBusinessConnection,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _command(*, account_id: str, message_id: str) -> TelegramBusinessTextParityCommand:
    return TelegramBusinessTextParityCommand(
        workspace_key="parity-workspace",
        account_id=account_id,
        canonical_message_id=message_id,
        update_id=42,
        account_alias="telegram_parity_test",
        direction=CanonicalMessageDirection.INBOUND,
        route=WorkRoute.HUMAN_REPLY_TASK,
        provider_dialog_id="private-dialog-never-persisted",
        business_connection_id="private-connection-never-persisted",
        text_content="private-text-never-persisted",
    )


@pytest.mark.anyio
async def test_postgres_evidence_is_scoped_content_free_and_restart_replay_safe() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL parity evidence test")

    schema = f"business_text_parity_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        workspace_id = uuid4()
        connector_id = uuid4()
        dialog_id = uuid4()
        message_id = uuid4()
        account_id = uuid4()
        now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
        async with sessions.begin() as session:
            session.add_all(
                [
                    Workspace(
                        id=workspace_id,
                        key="parity-workspace",
                        display_name="Parity workspace",
                        is_active=True,
                    ),
                    Account(
                        id=account_id,
                        alias="telegram_parity_test",
                        owner="parity-owner",
                        account_type="telegram_business",
                        permissions={},
                    ),
                ]
            )
            await session.flush()
            session.add(
                TelegramBusinessConnection(
                    account_id=account_id,
                    business_connection_id="private-connection-never-persisted",
                    rights={"can_reply": True},
                    is_enabled=True,
                )
            )
            session.add(
                TransportConnector(
                    id=connector_id,
                    workspace_id=workspace_id,
                    connector_key="telegram_business",
                    connector_kind="telegram_business",
                    is_enabled=True,
                )
            )
            await session.flush()
            session.add(
                Dialog(
                    id=dialog_id,
                    workspace_id=workspace_id,
                    connector_id=connector_id,
                    provider_dialog_id="private-dialog-never-persisted",
                    dialog_kind="private",
                )
            )
            await session.flush()
            session.add(
                CanonicalMessage(
                    id=message_id,
                    workspace_id=workspace_id,
                    dialog_id=dialog_id,
                    receipt_id=None,
                    provider_message_id="provider-message-never-in-evidence",
                    provider_sender_id="provider-sender-never-in-evidence",
                    direction="inbound",
                    text_content="private-text-never-persisted",
                    input_origin="telegram_text",
                    occurred_at=now,
                )
            )

        command = _command(account_id=str(account_id), message_id=str(message_id))
        async with sessions.begin() as session:
            await _record_evidence(
                session,
                command=command,
                state=TelegramBusinessTextParityState.PENDING,
                mismatch_code="telegram_business_text_parity_legacy_pending",
            )
        async with sessions.begin() as session:
            await _record_evidence(
                session,
                command=command,
                state=TelegramBusinessTextParityState.MATCHED,
                mismatch_code=None,
            )

        async with sessions() as session:
            evidence = (
                await session.execute(select(TelegramBusinessTextParityEvidence))
            ).scalar_one()
            status = await telegram_business_text_parity_status(
                session,
                workspace_key="parity-workspace",
                account_ids=frozenset({account_id}),
            )
            side_effect_counts = {
                "reply_tasks": await session.scalar(
                    select(func.count()).select_from(ReplyTask)
                ),
                "reply_drafts": await session.scalar(
                    select(func.count()).select_from(ReplyDraft)
                ),
                "agent_turns": await session.scalar(
                    select(func.count()).select_from(AgentTurnModel)
                ),
                "send_attempts": await session.scalar(
                    select(func.count()).select_from(SendAttempt)
                ),
            }

        assert evidence.state == "matched"
        assert evidence.mismatch_code is None
        assert evidence.observation_count == 2
        assert evidence.direction_policy_version == (
            TELEGRAM_BUSINESS_TEXT_PARITY_DIRECTION_POLICY_VERSION
        )
        assert evidence.account_id == account_id
        assert evidence.canonical_message_id == message_id
        assert status.payload() == {
            "state": "ready",
            "total_count": 1,
            "matched_count": 1,
            "pending_count": 0,
            "mismatch_count": 0,
            "required_account_count": 1,
            "covered_account_count": 1,
            "mismatch_codes": [],
            "cutover_ready": True,
            "private_content_exposed": False,
        }
        assert side_effect_counts == {
            "reply_tasks": 0,
            "reply_drafts": 0,
            "agent_turns": 0,
            "send_attempts": 0,
        }
        column_names = set(TelegramBusinessTextParityEvidence.__table__.columns.keys())
        assert column_names.isdisjoint(
            {
                "text",
                "text_content",
                "payload",
                "provider_dialog_id",
                "business_connection_id",
            }
        )
        assert "private" not in repr(evidence)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


def test_evidence_schema_has_scope_idempotency_and_privacy_guards() -> None:
    table = TelegramBusinessTextParityEvidence.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "fk_business_text_parity_message_workspace" in constraint_names
    assert "uq_business_text_parity_canonical_message" in constraint_names
    assert "ck_business_text_parity_state" in constraint_names
    assert "ck_business_text_parity_code_state" in constraint_names
    assert "ck_business_text_parity_direction_policy_version" in constraint_names
    assert "text_content" not in table.columns
    assert "provider_dialog_id" not in table.columns
    assert "business_connection_id" not in table.columns


def test_status_blocks_cutover_with_bounded_mismatch_counters() -> None:
    status = _status(
        matched=3,
        pending=1,
        mismatch=2,
        required_accounts=2,
        covered_accounts=2,
        mismatch_codes=(
            ("telegram_business_text_parity_dialog_mismatch", 2),
        ),
    )

    assert status.payload() == {
        "state": "blocked",
        "total_count": 6,
        "matched_count": 3,
        "pending_count": 1,
        "mismatch_count": 2,
        "required_account_count": 2,
        "covered_account_count": 2,
        "mismatch_codes": [
            {
                "code": "telegram_business_text_parity_dialog_mismatch",
                "count": 2,
            }
        ],
        "cutover_ready": False,
        "private_content_exposed": False,
    }
