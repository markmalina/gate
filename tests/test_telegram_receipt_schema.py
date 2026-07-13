"""Schema guards for durable Telegram receipts and transactional outbox."""

from app.models.domain import TelegramOutboxEvent, TelegramReceipt
from sqlalchemy import Index, UniqueConstraint


def _named_constraints(model: type) -> set[str | None]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _named_indexes(model: type) -> set[str | None]:
    return {
        index.name
        for index in model.__table__.indexes
        if isinstance(index, Index)
    }


def test_receipt_deduplicates_by_bot_and_update() -> None:
    assert "uq_telegram_receipts_bot_update" in _named_constraints(TelegramReceipt)
    assert "ix_telegram_receipts_status_next_attempt" in _named_indexes(
        TelegramReceipt
    )


def test_outbox_has_exactly_one_event_per_receipt() -> None:
    assert "uq_telegram_outbox_events_receipt_id" in _named_constraints(
        TelegramOutboxEvent
    )
    assert "ix_telegram_outbox_status_available" in _named_indexes(
        TelegramOutboxEvent
    )


def test_outbox_receipt_foreign_key_cascades_on_rollback_cleanup() -> None:
    foreign_keys = list(TelegramOutboxEvent.__table__.c.receipt_id.foreign_keys)

    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "telegram_receipts.id"
    assert foreign_keys[0].ondelete == "CASCADE"


def test_schema_defaults_start_in_received_and_pending_states() -> None:
    receipt_status = TelegramReceipt.__table__.c.status.server_default
    outbox_status = TelegramOutboxEvent.__table__.c.status.server_default

    assert receipt_status is not None
    assert outbox_status is not None
    assert "received" in str(receipt_status.arg)
    assert "pending" in str(outbox_status.arg)
