"""Composition tests for default-disabled canonical Telegram persistence."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from app.application.canonical_messaging import (
    CanonicalIngressResult,
    CanonicalMessageEnvelope,
    DialogIdentity,
    WorkOwnershipDecision,
    WorkRoute,
)
from app.application.telegram_ingress_identity import TelegramIngressIdentitySnapshot
from app.application.telegram_processing import (
    TelegramProcessingWorkItem,
    TelegramUpdateProcessingError,
)
from app.application.work_ownership import AgentKey, WorkOwnershipState
from app.core.config import Settings
from app.worker.telegram_processing import (
    ShadowClassifyingTelegramProcessor,
    run_telegram_worker_once,
)


class FakeIdentityReader:
    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        return TelegramIngressIdentitySnapshot(
            account_id="account-ru",
            account_alias="telegram_mark_ru",
            project_id="project-personal",
            owner_identity_ids=frozenset({"900000001"}),
        )


class CapturingCanonicalStore:
    def __init__(
        self,
        *,
        duplicate: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.duplicate = duplicate
        self.error = error
        self.calls: list[
            tuple[DialogIdentity, CanonicalMessageEnvelope, WorkOwnershipDecision]
        ] = []

    async def persist(
        self,
        *,
        dialog: DialogIdentity,
        message: CanonicalMessageEnvelope,
        ownership: WorkOwnershipDecision,
    ) -> CanonicalIngressResult:
        self.calls.append((dialog, message, ownership))
        if self.error is not None:
            raise self.error
        return CanonicalIngressResult(
            dialog_id="dialog-id",
            message_id="message-id",
            ownership_id="ownership-id",
            duplicate=self.duplicate,
        )


class ExplodingCanonicalStore:
    async def persist(self, **kwargs: object) -> CanonicalIngressResult:
        raise AssertionError("disabled worker must not access canonical storage")


class ExplodingQueue:
    async def claim_due(self, *, now: datetime) -> TelegramProcessingWorkItem | None:
        raise AssertionError("disabled worker must not access the queue")

    async def mark_processed(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("disabled worker must not mutate the queue")

    async def record_failure(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("disabled worker must not mutate the queue")


def _work_item(
    *,
    text: str = "Око возрождения",
    chat_id: object = 730000001,
) -> TelegramProcessingWorkItem:
    return TelegramProcessingWorkItem(
        receipt_id="00000000-0000-0000-0000-000000000061",
        outbox_event_id="00000000-0000-0000-0000-000000000062",
        bot_key="gate_m_bot",
        update_id=61,
        payload={
            "update_id": 61,
            "business_message": {
                "message_id": 71,
                "date": 1_784_086_400,
                "business_connection_id": "connection-ru",
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": 740000001, "is_bot": False},
                "text": text,
            },
        },
        received_at=datetime(2026, 7, 15, tzinfo=UTC),
        attempt=1,
    )


def _processor(
    store: CapturingCanonicalStore,
    *,
    runtime_logger: logging.Logger | None = None,
) -> ShadowClassifyingTelegramProcessor:
    return ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        identity_reader=FakeIdentityReader(),
        canonical_store=store,
        runtime_logger=runtime_logger or logging.getLogger("gate.canonical-worker-test"),
    )


def test_ru_oko_candidate_is_persisted_once_with_human_fallback() -> None:
    store = CapturingCanonicalStore()

    asyncio.run(_processor(store).process(_work_item()))

    assert len(store.calls) == 1
    dialog, message, ownership = store.calls[0]
    assert dialog.workspace_id == "mark-personal"
    assert message.source_receipt_id.endswith("61")
    assert ownership.state is WorkOwnershipState.HUMAN_MANAGED
    assert ownership.route is WorkRoute.HUMAN_REPLY_TASK
    assert ownership.candidate_agent is AgentKey.OKO


def test_duplicate_result_is_logged_only_as_a_boolean(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = CapturingCanonicalStore(duplicate=True)
    runtime_logger = logging.getLogger("gate.canonical-worker-duplicate-test")

    with caplog.at_level(logging.INFO, logger=runtime_logger.name):
        asyncio.run(_processor(store, runtime_logger=runtime_logger).process(_work_item()))

    assert "canonical_persisted=True" in caplog.text
    assert "canonical_duplicate=True" in caplog.text
    assert "Око возрождения" not in caplog.text
    assert "730000001" not in caplog.text
    assert "740000001" not in caplog.text
    assert "connection-ru" not in caplog.text


def test_canonical_store_failure_is_bounded_and_has_no_private_cause() -> None:
    store = CapturingCanonicalStore(
        error=RuntimeError("private SQL text 730000001 Око возрождения")
    )

    with pytest.raises(TelegramUpdateProcessingError) as captured:
        asyncio.run(_processor(store).process(_work_item()))

    assert captured.value.error_code == "canonical_ingress_persist_failed"
    assert captured.value.__cause__ is None
    assert "private" not in str(captured.value)


def test_invalid_provider_identity_fails_before_canonical_storage() -> None:
    store = CapturingCanonicalStore()

    with pytest.raises(TelegramUpdateProcessingError) as captured:
        asyncio.run(_processor(store).process(_work_item(chat_id="untrusted")))

    assert captured.value.error_code == "canonical_projection_failed"
    assert captured.value.__cause__ is None
    assert store.calls == []


def test_control_command_creates_no_canonical_fact() -> None:
    store = CapturingCanonicalStore()

    asyncio.run(_processor(store).process(_work_item(text="/status")))

    assert store.calls == []


def test_disabled_worker_touches_neither_queue_nor_canonical_storage() -> None:
    result = asyncio.run(
        run_telegram_worker_once(
            settings=Settings(_env_file=None),
            queue=ExplodingQueue(),
            canonical_store=ExplodingCanonicalStore(),
        )
    )

    assert result.enabled is False
    assert result.disposition == "disabled"


def test_workspace_id_is_bounded_configuration_not_payload_data() -> None:
    settings = Settings(_env_file=None, GATE_WORKSPACE_ID="workspace-two")

    assert settings.gate_workspace_id == "workspace-two"
    with pytest.raises(ValueError):
        Settings(_env_file=None, GATE_WORKSPACE_ID="")
