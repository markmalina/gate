"""Bounded PostgreSQL recovery proof for Alpha membership dead letters."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.telegram_processing import (
    ALPHA_GROUP_CAPACITY_ERROR_CODE,
    ALPHA_GROUP_CAPACITY_ERROR_CODES,
    ALPHA_GROUP_LEGACY_CAPACITY_ERROR_CODE,
    is_alpha_membership_update,
)
from app.core.config import Settings, get_settings
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.telegram_processing import (
    SqlAlphaCapacityDeadLetterReplayer,
)
from app.models.domain import (
    Base,
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
)
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 7, 25, 21, 30, tzinfo=UTC)


def _membership(update_id: int, chat_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "my_chat_member": {
            "chat": {
                "id": chat_id,
                "type": "supergroup",
                "title": "redacted test group",
            },
            "new_chat_member": {"status": "member"},
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"update_id": 7, "message": {}},
        {"update_id": 8, "my_chat_member": {}},
        {
            "update_id": 7,
            "my_chat_member": {
                "chat": {"id": -1007, "type": "private"},
                "new_chat_member": {"status": "member"},
            },
        },
        {
            "update_id": 7,
            "my_chat_member": {
                "chat": {"id": True, "type": "supergroup"},
                "new_chat_member": {"status": "member"},
            },
        },
    ],
)
def test_membership_replay_predicate_rejects_non_exact_updates(
    payload: dict[str, object],
) -> None:
    assert is_alpha_membership_update(payload, update_id=7) is False


def test_membership_replay_predicate_accepts_exact_group_update() -> None:
    assert is_alpha_membership_update(_membership(7, -1007), update_id=7) is True


def test_capacity_error_allowlist_is_exact_and_backward_compatible() -> None:
    assert ALPHA_GROUP_CAPACITY_ERROR_CODES == {
        "alpha_canary_group_capacity_reached",
        "alpha_canary_group_already_pinned",
    }


def test_membership_replay_is_default_off_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).gate_alpha_membership_replay_limit == 0

    monkeypatch.setenv("GATE_ALPHA_MEMBERSHIP_REPLAY_LIMIT", "2")
    assert Settings(_env_file=None).gate_alpha_membership_replay_limit == 2


@pytest.mark.parametrize("limit", [-1, 33])
def test_membership_replay_rejects_out_of_range_configuration(
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    monkeypatch.setenv("GATE_ALPHA_MEMBERSHIP_REPLAY_LIMIT", str(limit))

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.anyio
async def test_replay_is_filtered_bounded_transactional_and_idempotent() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Alpha replay test")

    schema = f"alpha_membership_replay_{uuid4().hex}"
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
        cases = (
            (
                100,
                _membership(100, -100100),
                TelegramConnectorKey.ALPHA_GROUP.value,
                ALPHA_GROUP_CAPACITY_ERROR_CODE,
                ALPHA_GROUP_CAPACITY_ERROR_CODE,
            ),
            (
                101,
                _membership(101, -100101),
                TelegramConnectorKey.ALPHA_GROUP.value,
                ALPHA_GROUP_LEGACY_CAPACITY_ERROR_CODE,
                ALPHA_GROUP_LEGACY_CAPACITY_ERROR_CODE,
            ),
            (
                102,
                {"update_id": 102, "message": {}},
                TelegramConnectorKey.ALPHA_GROUP.value,
                ALPHA_GROUP_CAPACITY_ERROR_CODE,
                ALPHA_GROUP_CAPACITY_ERROR_CODE,
            ),
            (
                103,
                _membership(103, -100103),
                TelegramConnectorKey.ALPHA_GROUP.value,
                "different_failure",
                "different_failure",
            ),
            (
                104,
                _membership(104, -100104),
                TelegramConnectorKey.BUSINESS.value,
                ALPHA_GROUP_CAPACITY_ERROR_CODE,
                ALPHA_GROUP_CAPACITY_ERROR_CODE,
            ),
            (
                105,
                _membership(105, -100105),
                TelegramConnectorKey.ALPHA_GROUP.value,
                ALPHA_GROUP_CAPACITY_ERROR_CODE,
                ALPHA_GROUP_LEGACY_CAPACITY_ERROR_CODE,
            ),
        )
        async with sessions.begin() as seed:
            for offset, (
                update_id,
                payload,
                bot_key,
                receipt_error_code,
                outbox_error_code,
            ) in enumerate(cases):
                received_at = NOW + timedelta(seconds=offset)
                receipt = TelegramReceipt(
                    bot_key=bot_key,
                    update_id=update_id,
                    payload=payload,
                    status=TelegramReceiptStatus.DEAD_LETTER.value,
                    attempt_count=5,
                    received_at=received_at,
                    last_error_code=receipt_error_code,
                    last_error_at=received_at,
                )
                seed.add(receipt)
                await seed.flush()
                seed.add(
                    TelegramOutboxEvent(
                        receipt_id=receipt.id,
                        status=TelegramOutboxStatus.DEAD_LETTER.value,
                        attempt_count=5,
                        available_at=received_at,
                        last_error_code=outbox_error_code,
                        last_error_at=received_at,
                        locked_at=received_at,
                    )
                )

        async with sessions() as session:
            replayer = SqlAlphaCapacityDeadLetterReplayer(session)
            assert (
                await replayer.requeue(
                    bot_key=TelegramConnectorKey.ALPHA_GROUP.value,
                    now=NOW + timedelta(minutes=1),
                    limit=2,
                )
                == 2
            )
            assert (
                await replayer.requeue(
                    bot_key=TelegramConnectorKey.ALPHA_GROUP.value,
                    now=NOW + timedelta(minutes=2),
                    limit=2,
                )
                == 0
            )

        async with sessions() as session:
            rows = (
                await session.execute(
                    select(TelegramOutboxEvent, TelegramReceipt)
                    .join(
                        TelegramReceipt,
                        TelegramReceipt.id == TelegramOutboxEvent.receipt_id,
                    )
                    .order_by(TelegramReceipt.update_id)
                )
            ).all()

        states = {
            receipt.update_id: (event, receipt)
            for event, receipt in rows
        }
        for update_id in (100, 101):
            event, receipt = states[update_id]
            assert event.status == TelegramOutboxStatus.PENDING.value
            assert receipt.status == TelegramReceiptStatus.RECEIVED.value
            assert event.attempt_count == receipt.attempt_count == 0
            assert event.last_error_code is receipt.last_error_code is None
            assert event.locked_at is None
        for update_id in (102, 103, 104, 105):
            event, receipt = states[update_id]
            assert event.status == TelegramOutboxStatus.DEAD_LETTER.value
            assert receipt.status == TelegramReceiptStatus.DEAD_LETTER.value
            assert event.attempt_count == receipt.attempt_count == 5
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
        await admin_engine.dispose()
