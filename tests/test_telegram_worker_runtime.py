"""Safety guards for the disabled-by-default Telegram worker runtime."""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from app.application.telegram_ingress_identity import TelegramIngressIdentitySnapshot
from app.application.telegram_processing import (
    TelegramProcessingPlan,
    TelegramProcessingWorkItem,
    TelegramUpdateProcessingError,
)
from app.core.config import Settings
from app.worker import telegram_processing as worker_module
from app.worker.telegram_processing import (
    ShadowClassifyingTelegramProcessor,
    TelegramWorkerRuntimeResult,
    run_telegram_worker_loop,
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


class FakeIdentityReader:
    def __init__(
        self,
        snapshot: TelegramIngressIdentitySnapshot | None = None,
        error: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error
        self.connection_ids: list[str] = []

    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        self.connection_ids.append(business_connection_id)
        if self.error is not None:
            raise self.error
        return self.snapshot


class SingleItemQueue:
    def __init__(self, item: TelegramProcessingWorkItem) -> None:
        self.item = item
        self.failure_plan: TelegramProcessingPlan | None = None

    async def claim_due(self, *, now: datetime) -> TelegramProcessingWorkItem | None:
        return self.item

    async def mark_processed(
        self,
        item: TelegramProcessingWorkItem,
        *,
        processed_at: datetime,
    ) -> None:
        raise AssertionError("a failed identity read cannot mark the item processed")

    async def record_failure(
        self,
        item: TelegramProcessingWorkItem,
        *,
        failed_at: datetime,
        plan: TelegramProcessingPlan,
    ) -> None:
        self.failure_plan = plan


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
                "chat": {"id": "private-chat", "type": "private"},
                "from": {"id": 700000001, "is_bot": False},
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

    result = asyncio.run(
        run_telegram_worker_once(
            settings=settings,
            queue=queue,
            identity_reader=FakeIdentityReader(),
        )
    )

    assert result.enabled is True
    assert result.disposition == "idle"
    assert queue.claim_count == 1


def test_disabled_worker_loop_never_runs_an_iteration() -> None:
    calls = 0

    async def iteration(_settings: Settings) -> TelegramWorkerRuntimeResult:
        nonlocal calls
        calls += 1
        return TelegramWorkerRuntimeResult(enabled=True, disposition="idle")

    asyncio.run(
        run_telegram_worker_loop(
            Settings(_env_file=None),
            run_iteration=iteration,
        )
    )

    assert calls == 0


def test_enabled_worker_loop_drains_until_stopped() -> None:
    calls = 0

    async def scenario() -> None:
        nonlocal calls
        stop = asyncio.Event()

        async def iteration(_settings: Settings) -> TelegramWorkerRuntimeResult:
            nonlocal calls
            calls += 1
            if calls == 2:
                stop.set()
            return TelegramWorkerRuntimeResult(enabled=True, disposition="processed")

        await run_telegram_worker_loop(
            Settings(_env_file=None, GATE_TELEGRAM_WORKER_ENABLED=True),
            stop_event=stop,
            run_iteration=iteration,
        )

    asyncio.run(scenario())

    assert calls == 2


def test_shadow_processor_logs_only_redacted_classification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime_logger = logging.getLogger("gate.worker.test")
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        identity_reader=FakeIdentityReader(),
        runtime_logger=runtime_logger,
    )

    with caplog.at_level(logging.INFO, logger=runtime_logger.name):
        asyncio.run(processor.process(_work_item()))

    wire = caplog.text
    assert "business_update" in wire
    assert "configured_account_resolution=database_lookup_required" in wire
    assert "identity_resolution=connection_not_found" in wire
    assert "identity_reader_used=True" in wire
    assert "direction_evidence=unknown" in wire
    assert "ownership_proposed_state=quarantined" in wire
    assert "ownership_candidate=none" in wire
    assert "ownership_reason=unresolved_context" in wire
    assert "ownership_signal=none" in wire
    assert "ownership_policy=work-ownership-shadow-v1" in wire
    assert "ownership_authoritative=False" in wire
    assert "private-message-text" not in wire
    assert "private-connection" not in wire
    assert "private-chat" not in wire
    assert "700000001" not in wire
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
        identity_reader=FakeIdentityReader(),
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
        identity_reader=FakeIdentityReader(),
        runtime_logger=runtime_logger,
    )

    with caplog.at_level(logging.INFO, logger=runtime_logger.name):
        asyncio.run(processor.process(item))

    assert "ownership_candidate=none" in caplog.text
    assert "ownership_signal=none" in caplog.text


def test_database_snapshot_wins_over_static_connection_mapping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reader = FakeIdentityReader(
        TelegramIngressIdentitySnapshot(
            account_id="database-account-by",
            account_alias="telegram_mark_by",
            project_id="database-project",
            owner_identity_ids=frozenset({"database-owner"}),
        )
    )
    settings = Settings(
        _env_file=None,
        GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID="private-connection",
    )
    runtime_logger = logging.getLogger("gate.worker.database-snapshot-wins-test")
    processor = ShadowClassifyingTelegramProcessor(
        settings=settings,
        identity_reader=reader,
        runtime_logger=runtime_logger,
    )
    item = _work_item()
    item.payload["business_message"]["text"] = "Око возрождения"

    with caplog.at_level(logging.INFO, logger=runtime_logger.name):
        asyncio.run(processor.process(item))

    assert "configured_account_resolution=configured_connection" in caplog.text
    assert "identity_resolution=database_snapshot" in caplog.text
    assert "ownership_reason=by_deferred" in caplog.text


def test_identity_reader_failure_uses_bounded_retryable_code_without_private_detail() -> None:
    reader = FakeIdentityReader(
        error=RuntimeError("private SQL parameters private-connection private-sender")
    )
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        identity_reader=reader,
        runtime_logger=logging.getLogger("gate.worker.identity-failure-test"),
    )

    with pytest.raises(TelegramUpdateProcessingError) as captured:
        asyncio.run(processor.process(_work_item()))

    assert captured.value.error_code == "identity_snapshot_failed"
    assert captured.value.__cause__ is None
    assert "private" not in str(captured.value)
    assert reader.connection_ids == ["private-connection"]


def test_identity_reader_failure_is_persisted_through_queue_retry_lifecycle() -> None:
    settings = Settings(_env_file=None, GATE_TELEGRAM_WORKER_ENABLED=True)
    queue = SingleItemQueue(_work_item())

    result = asyncio.run(
        run_telegram_worker_once(
            settings=settings,
            queue=queue,
            identity_reader=FakeIdentityReader(error=RuntimeError("private SQL detail")),
            now=datetime(2026, 7, 15, tzinfo=UTC),
        )
    )

    assert result.disposition == "retry"
    assert result.error_code == "identity_snapshot_failed"
    assert queue.failure_plan is not None
    assert queue.failure_plan.error_code == "identity_snapshot_failed"


def test_control_shape_never_reads_business_identity() -> None:
    reader = FakeIdentityReader(error=AssertionError("identity reader must not be called"))
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        identity_reader=reader,
        runtime_logger=logging.getLogger("gate.worker.no-identity-read-test"),
    )
    control = replace(
        _work_item(),
        payload={
            "update_id": 42,
            "business_connection": {"id": "private-connection"},
        },
    )

    asyncio.run(processor.process(control))

    assert reader.connection_ids == []


@pytest.mark.parametrize(
    "command",
    (
        "/draft deadbeef",
        "/send deadbeef",
        "/inbox",
        "/raw",
        "/events",
        "/business ru synthetic-connection",
        "/status",
        "/edit deadbeef",
        "/senddraft deadbeef",
    ),
)
def test_business_control_command_with_connection_id_never_reads_identity(
    command: str,
) -> None:
    reader = FakeIdentityReader(error=AssertionError("identity reader must not be called"))
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        identity_reader=reader,
        runtime_logger=logging.getLogger("gate.worker.no-command-identity-read-test"),
    )
    control = replace(
        _work_item(),
        payload={
            "update_id": 42,
            "business_message": {
                "message_id": 7,
                "business_connection_id": "private-connection",
                "chat": {"id": "private-chat", "type": "private"},
                "from": {"id": 700000001, "is_bot": False},
                "text": command,
            },
        },
    )

    asyncio.run(processor.process(control))

    assert reader.connection_ids == []
