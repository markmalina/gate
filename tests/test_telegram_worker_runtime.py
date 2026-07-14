"""Safety guards for the disabled-by-default Telegram worker runtime."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from app.application.telegram_processing import (
    TelegramProcessingWorkItem,
    TelegramUpdateProcessingError,
)
from app.core.config import Settings
from app.worker import telegram_processing as worker_module
from app.worker.telegram_processing import (
    ShadowClassifyingTelegramProcessor,
    run_telegram_worker_once,
)


class ExplodingQueue:
    async def claim_due(self, *, now: datetime) -> TelegramProcessingWorkItem | None:
        raise AssertionError("disabled runtime must not access the queue")

    async def mark_processed(
        self,
        item: TelegramProcessingWorkItem,
        *,
        processed_at: datetime,
    ) -> None:
        raise AssertionError("disabled runtime must not mutate the queue")

    async def record_failure(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("disabled runtime must not mutate the queue")


class EmptyQueue:
    def __init__(self) -> None:
        self.claim_count = 0

    async def claim_due(self, *, now: datetime) -> TelegramProcessingWorkItem | None:
        self.claim_count += 1
        return None

    async def mark_processed(
        self,
        item: TelegramProcessingWorkItem,
        *,
        processed_at: datetime,
    ) -> None:
        raise AssertionError("empty queue cannot mark processed")

    async def record_failure(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("empty queue cannot record failure")


def _work_item(*, bot_key: str = "gate_m_bot") -> TelegramProcessingWorkItem:
    return TelegramProcessingWorkItem(
        receipt_id="receipt-1",
        outbox_event_id="outbox-1",
        bot_key=bot_key,
        update_id=42,
        payload={
            "update_id": 42,
            "business_message": {
                "message_id": 7,
                "business_connection_id": "private-connection",
                "chat": {"id": "private-chat"},
                "from": {"id": "private-sender"},
                "text": "private-message-text",
            },
        },
        received_at=datetime(2026, 7, 14, tzinfo=UTC),
        attempt=1,
    )


def test_worker_is_disabled_by_default_and_never_claims() -> None:
    settings = Settings(_env_file=None)

    result = asyncio.run(
        run_telegram_worker_once(settings=settings, queue=ExplodingQueue())
    )

    assert settings.gate_telegram_worker_enabled is False
    assert result.enabled is False
    assert result.disposition == "disabled"


def test_explicitly_enabled_worker_runs_at_most_one_claim() -> None:
    settings = Settings(_env_file=None, GATE_TELEGRAM_WORKER_ENABLED=True)
    queue = EmptyQueue()

    result = asyncio.run(run_telegram_worker_once(settings=settings, queue=queue))

    assert result.enabled is True
    assert result.disposition == "idle"
    assert queue.claim_count == 1


def test_shadow_processor_logs_only_redacted_classification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime_logger = logging.getLogger("gate.worker.test")
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        runtime_logger=runtime_logger,
    )

    with caplog.at_level(logging.INFO, logger=runtime_logger.name):
        asyncio.run(processor.process(_work_item()))

    wire = caplog.text
    assert "business_update" in wire
    assert "database_lookup_required" in wire
    assert "ownership_proposed_state=quarantined" in wire
    assert "ownership_candidate=none" in wire
    assert "ownership_reason=unresolved_context" in wire
    assert "ownership_signal=none" in wire
    assert "ownership_policy=work-ownership-shadow-v1" in wire
    assert "ownership_authoritative=False" in wire
    assert "private-message-text" not in wire
    assert "private-connection" not in wire
    assert "private-chat" not in wire
    assert "private-sender" not in wire
    assert "update_id=42" not in wire


@pytest.mark.parametrize(
    ("bot_key", "error_code"),
    [
        ("unknown_bot", "unknown_telegram_connector"),
        ("telegram_alpha_group", "telegram_connector_disabled"),
    ],
)
def test_shadow_processor_fails_closed_before_classification_for_untrusted_connector(
    bot_key: str,
    error_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_classification(_payload: object) -> None:
        raise AssertionError("untrusted connector reached payload classification")

    monkeypatch.setattr(worker_module, "classify_telegram_ingress", fail_classification)
    runtime_logger = logging.getLogger("gate.worker.connector-test")
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        runtime_logger=runtime_logger,
    )

    with pytest.raises(TelegramUpdateProcessingError) as captured:
        asyncio.run(processor.process(_work_item(bot_key=bot_key)))

    assert captured.value.error_code == error_code


def test_payload_cannot_override_trusted_business_connector_role(
    caplog: pytest.LogCaptureFixture,
) -> None:
    item = _work_item()
    item.payload["connector_role"] = "alpha_group"
    item.payload["bot_key"] = "telegram_alpha_group"
    runtime_logger = logging.getLogger("gate.worker.payload-role-test")
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        runtime_logger=runtime_logger,
    )

    with caplog.at_level(logging.INFO, logger=runtime_logger.name):
        asyncio.run(processor.process(item))

    assert "ownership_candidate=none" in caplog.text
    assert "ownership_signal=none" in caplog.text
