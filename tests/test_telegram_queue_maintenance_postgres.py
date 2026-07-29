"""PostgreSQL guards for Telegram queue health and stale recovery."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.telegram_queue_maintenance import (
    RecoverStaleTelegramClaimsCommand,
    TelegramStaleClaimRecovery,
)
from app.core.config import get_settings
from app.infrastructure.telegram_queue_maintenance import (
    SqlTelegramQueueHealthReader,
    SqlTelegramQueueMaintenance,
)
from app.models.domain import (
    Account,
    AuditLog,
    Base,
    Project,
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
    TelegramVoiceArchive,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health_and_recovery_are_bounded_and_redacted() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL queue maintenance test")

    schema = f"queue_maintenance_{uuid4().hex}"
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
        now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

        async with sessions() as session:
            for update_id, attempt_count in ((1, 2), (2, 5)):
                receipt = TelegramReceipt(
                    id=uuid4(),
                    bot_key="gate_m_bot",
                    update_id=update_id,
                    payload={"update_id": update_id, "text": "must-not-leak"},
                    status=TelegramReceiptStatus.PROCESSING.value,
                    attempt_count=attempt_count,
                    received_at=now - timedelta(minutes=10),
                    processing_started_at=now - timedelta(minutes=9),
                )
                session.add(receipt)
                await session.flush()
                session.add(
                    TelegramOutboxEvent(
                        id=uuid4(),
                        receipt_id=receipt.id,
                        status=TelegramOutboxStatus.PROCESSING.value,
                        attempt_count=attempt_count,
                        available_at=now - timedelta(minutes=10),
                        locked_at=now - timedelta(minutes=9),
                    )
                )
            await session.commit()

        stale_before = now - timedelta(minutes=5)
        async with sessions() as session:
            health = await SqlTelegramQueueHealthReader(session).read(
                now=now,
                stale_before=stale_before,
            )
        assert health.processing == 2
        assert health.stale_processing == 2
        assert not hasattr(health, "payload")

        async with sessions() as session:
            recovery = await SqlTelegramQueueMaintenance(session).recover_stale(
                RecoverStaleTelegramClaimsCommand(
                    stale_before=stale_before,
                    recovered_at=now,
                    max_attempts=5,
                    limit=100,
                )
            )
        assert recovery.recovered == 2
        assert recovery.retry_scheduled == 1
        assert recovery.dead_lettered == 1

        async with sessions() as session:
            receipts = (
                await session.execute(select(TelegramReceipt).order_by(TelegramReceipt.update_id))
            ).scalars().all()
            events = (
                await session.execute(
                    select(TelegramOutboxEvent).order_by(TelegramOutboxEvent.attempt_count)
                )
            ).scalars().all()
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action
                        == "telegram_queue_stale_claims_recovered"
                    )
                )
            ).scalar_one()
        assert [receipt.status for receipt in receipts] == [
            TelegramReceiptStatus.RECEIVED.value,
            TelegramReceiptStatus.DEAD_LETTER.value,
        ]
        assert [event.status for event in events] == [
            TelegramOutboxStatus.RETRY_SCHEDULED.value,
            TelegramOutboxStatus.DEAD_LETTER.value,
        ]
        assert events[0].available_at == now
        assert events[0].locked_at is None
        assert receipts[1].last_error_code == "worker_lease_expired"
        assert audit.actor == "gate-telegram-worker"
        assert audit.account_id is None
        assert audit.project_id is None
        assert audit.metadata_ == {
            "recovered": 2,
            "retry_scheduled": 1,
            "dead_lettered": 1,
            "max_attempts": 5,
            "limit": 100,
            "persisted_voice_transcript_required": False,
        }
        assert "payload" not in audit.metadata_
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.anyio
async def test_persisted_voice_recovery_is_exact_and_provider_free() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL voice recovery test")

    schema = f"voice_stale_recovery_{uuid4().hex}"
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
        now = datetime(2026, 7, 29, 11, 0, tzinfo=UTC)

        async with sessions() as session:
            project = Project(
                id=uuid4(),
                alias="voice-stale-recovery",
                name="Voice stale recovery",
                adapter_type="test",
            )
            account = Account(
                id=uuid4(),
                alias="voice-stale-recovery-account",
                owner="owner",
                account_type="telegram_business",
                linked_project_id=project.id,
            )
            session.add_all([project, account])
            for update_id in (1, 2):
                receipt = TelegramReceipt(
                    id=uuid4(),
                    bot_key="telegram_business",
                    update_id=update_id,
                    payload={
                        "update_id": update_id,
                        "business_message": {"voice": {"duration": 3}},
                    },
                    status=TelegramReceiptStatus.PROCESSING.value,
                    attempt_count=1,
                    received_at=now - timedelta(minutes=10),
                    processing_started_at=now - timedelta(minutes=9),
                )
                session.add(receipt)
                await session.flush()
                session.add_all(
                    [
                        TelegramOutboxEvent(
                            id=uuid4(),
                            receipt_id=receipt.id,
                            status=TelegramOutboxStatus.PROCESSING.value,
                            attempt_count=1,
                            available_at=now - timedelta(minutes=10),
                            locked_at=now - timedelta(minutes=9),
                        ),
                        TelegramVoiceArchive(
                            id=uuid4(),
                            receipt_id=receipt.id,
                            account_id=account.id,
                            project_id=project.id,
                            direction="outbound",
                            telegram_update_id=update_id,
                            telegram_message_id=update_id,
                            chat_id=f"test-chat-{update_id}",
                            business_connection_id=f"test-connection-{update_id}",
                            duration_seconds=3,
                            filename=f"voice-{update_id}.ogg",
                            media_type="audio/ogg",
                            size_bytes=5,
                            sha256=f"{update_id:064x}",
                            data=b"audio",
                            transcription_status="transcribed",
                            transcript_text="stored transcript",
                            transcript_language="ru",
                            transcription_model="test-model",
                            transcription_reason_code="telegram_voice_transcribed",
                            transcribed_at=now - timedelta(minutes=10),
                            received_at=now - timedelta(minutes=10),
                        ),
                    ]
                )
            await session.commit()

        command = RecoverStaleTelegramClaimsCommand(
            stale_before=now - timedelta(minutes=5),
            recovered_at=now,
            max_attempts=5,
            limit=1,
            require_persisted_voice_transcript=True,
        )
        async with sessions() as session:
            with pytest.raises(
                RuntimeError,
                match="telegram_stale_transcribed_voice_scope_ambiguous",
            ):
                await SqlTelegramQueueMaintenance(session).recover_stale(command)

        async with sessions() as session:
            archives = (
                await session.execute(
                    select(TelegramVoiceArchive).order_by(
                        TelegramVoiceArchive.telegram_update_id
                    )
                )
            ).scalars().all()
            archives[1].transcription_status = "unknown"
            archives[1].transcript_text = None
            await session.commit()

        async with sessions() as session:
            recovery = await SqlTelegramQueueMaintenance(session).recover_stale(
                command
            )
        assert recovery == TelegramStaleClaimRecovery(
            recovered=1,
            retry_scheduled=1,
            dead_lettered=0,
        )

        async with sessions() as session:
            receipts = (
                await session.execute(
                    select(TelegramReceipt).order_by(TelegramReceipt.update_id)
                )
            ).scalars().all()
            events = (
                await session.execute(
                    select(TelegramOutboxEvent)
                    .join(
                        TelegramReceipt,
                        TelegramReceipt.id == TelegramOutboxEvent.receipt_id,
                    )
                    .order_by(TelegramReceipt.update_id)
                )
            ).scalars().all()
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action
                        == "telegram_queue_stale_claims_recovered"
                    )
                )
            ).scalar_one()
        assert [receipt.status for receipt in receipts] == [
            TelegramReceiptStatus.RECEIVED.value,
            TelegramReceiptStatus.PROCESSING.value,
        ]
        assert [event.status for event in events] == [
            TelegramOutboxStatus.RETRY_SCHEDULED.value,
            TelegramOutboxStatus.PROCESSING.value,
        ]
        assert receipts[0].last_error_code == (
            "worker_lease_expired_transcript_persisted"
        )
        assert audit.metadata_["persisted_voice_transcript_required"] is True
        assert "payload" not in audit.metadata_
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
