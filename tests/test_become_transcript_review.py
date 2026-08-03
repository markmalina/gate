from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
    opaque_subject_ref,
)
from app.application.become_transcript_review import (
    DeliverPilotTranscriptReview,
    PilotDeliveryJob,
    PilotReviewDisposition,
    ProcessPilotTranscriptReviewUpdate,
    delivery_body,
    extract_pilot_review_update,
    parse_callback_data,
)
from app.worker.telegram_processing import _is_become_pilot_review_payload

NOW = datetime(2026, 8, 3, 15, 30, tzinfo=UTC)
PEPPER = "test-only-pepper"
TOKEN = "0123456789abcdef0123456789abcdef"


def _callback(
    *,
    action: str = "c",
    actor_id: int = 10,
    chat_id: int = -100,
    token: str = TOKEN,
) -> dict[str, Any]:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": actor_id, "is_bot": False},
            "data": f"b1:{action}:{token}",
            "message": {
                "message_id": 9,
                "chat": {"id": chat_id, "type": "supergroup"},
            },
        },
    }


def _correction(
    *,
    text: str = "Исправленный текст",
    actor_id: int = 10,
    chat_id: int = -100,
) -> dict[str, Any]:
    return {
        "update_id": 2,
        "message": {
            "message_id": 10,
            "from": {"id": actor_id, "is_bot": False},
            "chat": {"id": chat_id, "type": "supergroup"},
            "text": text,
        },
    }


class FakeStore:
    def __init__(self) -> None:
        self.expected_subject = opaque_subject_ref(
            provider_actor_id="10",
            pepper=PEPPER,
        )
        self.expected_chat = "-100"
        self.callback_state = PilotReviewDisposition.APPROVED
        self.correction_pending = True
        self.callbacks = 0
        self.corrections: list[str] = []
        self.tombstones: list[str] = []
        self.jobs: list[PilotDeliveryJob] = []
        self.settlements: list[tuple[str, str, str | None]] = []

    async def apply_callback(
        self,
        *,
        action: str,
        callback_ref: str,
        subject_ref: str,
        provider_dialog_id: str,
        now: datetime,
    ) -> PilotReviewDisposition:
        if subject_ref != self.expected_subject:
            raise BecomeControlledIntakeError("wrong_actor")
        if provider_dialog_id != self.expected_chat:
            raise BecomeControlledIntakeError("wrong_chat")
        if callback_ref != TOKEN:
            raise BecomeControlledIntakeError("mutated_callback")
        if action not in {"c", "e"}:
            raise BecomeControlledIntakeError("wrong_action")
        self.callbacks += 1
        return self.callback_state

    async def apply_correction(
        self,
        *,
        subject_ref: str,
        provider_dialog_id: str,
        correction_text: str,
        now: datetime,
    ) -> bool:
        if subject_ref != self.expected_subject:
            raise BecomeControlledIntakeError("wrong_actor")
        if provider_dialog_id != self.expected_chat:
            raise BecomeControlledIntakeError("wrong_chat")
        if not self.correction_pending:
            return False
        self.corrections.append(correction_text)
        self.correction_pending = False
        return True

    async def claim_delivery(
        self,
        *,
        now: datetime,
        max_operations: int,
    ) -> PilotDeliveryJob | None:
        if max_operations < 1 or not self.jobs:
            return None
        return self.jobs.pop(0)

    async def settle_delivery(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        now: datetime,
    ) -> None:
        self.settlements.append(
            (delivery_id, outcome, telegram_message_id)
        )

    async def tombstone_receipt(
        self,
        *,
        receipt_id: str,
        now: datetime,
    ) -> None:
        self.tombstones.append(receipt_id)


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.callbacks: list[str] = []
        self.fail = False
        self.invalid_success = False

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )
        if self.fail:
            raise RuntimeError("transport outcome unknown")
        if self.invalid_success:
            return {"ok": True, "result": {}}
        return {"ok": True, "result": {"message_id": 11}}

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        self.callbacks.append(callback_query_id)
        return {"ok": True}


def test_callback_parser_binds_exact_group_actor() -> None:
    event = extract_pilot_review_update(_callback(), pepper=PEPPER)
    assert event is not None
    assert event.action == "c"
    assert event.callback_ref == TOKEN
    assert event.actor.provider_dialog_id == "-100"
    assert event.actor.subject_ref == opaque_subject_ref(
        provider_actor_id="10",
        pepper=PEPPER,
    )


@pytest.mark.parametrize(
    "update",
    [
        {
            "update_id": 1,
            "callback_query": {
                "id": "x",
                "from": {"id": 10, "is_bot": False},
                "data": f"b1:c:{TOKEN}",
                "message": {"chat": {"id": 1, "type": "private"}},
            },
        },
        _callback(token="f" * 31 + "z"),
        _callback(token="f" * 31),
    ],
)
def test_wrong_workspace_and_mutated_callback_fail_closed(
    update: dict[str, Any],
) -> None:
    with pytest.raises(BecomeControlledIntakeError):
        extract_pilot_review_update(update, pepper=PEPPER)


@pytest.mark.anyio
async def test_wrong_actor_callback_fails_closed() -> None:
    store = FakeStore()
    service = ProcessPilotTranscriptReviewUpdate(
        store=store,
        telegram=FakeTelegram(),
        identity_pepper=PEPPER,
        enabled=True,
    )
    with pytest.raises(BecomeControlledIntakeError, match="wrong_actor"):
        await service.execute(update=_callback(actor_id=11), now=NOW)


@pytest.mark.anyio
async def test_wrong_chat_callback_fails_closed() -> None:
    store = FakeStore()
    service = ProcessPilotTranscriptReviewUpdate(
        store=store,
        telegram=FakeTelegram(),
        identity_pepper=PEPPER,
        enabled=True,
    )
    with pytest.raises(BecomeControlledIntakeError, match="wrong_chat"):
        await service.execute(update=_callback(chat_id=-200), now=NOW)


@pytest.mark.anyio
async def test_stale_well_formed_callback_fails_closed() -> None:
    service = ProcessPilotTranscriptReviewUpdate(
        store=FakeStore(),
        telegram=FakeTelegram(),
        identity_pepper=PEPPER,
        enabled=True,
    )
    with pytest.raises(BecomeControlledIntakeError, match="mutated_callback"):
        await service.execute(
            update=_callback(token="f" * 32),
            now=NOW,
        )


def test_review_payload_classifier_separates_commands_from_private_content() -> None:
    assert _is_become_pilot_review_payload(_callback()) is True
    assert _is_become_pilot_review_payload(_correction()) is True
    command = _correction(text="/become@BecomeNowBot")
    assert _is_become_pilot_review_payload(command) is False


@pytest.mark.anyio
async def test_exact_confirm_and_duplicate_confirm_are_visible_noops() -> None:
    store = FakeStore()
    telegram = FakeTelegram()
    service = ProcessPilotTranscriptReviewUpdate(
        store=store,
        telegram=telegram,
        identity_pepper=PEPPER,
        enabled=True,
    )
    first = await service.execute(update=_callback(), now=NOW)
    assert first.disposition is PilotReviewDisposition.APPROVED
    store.callback_state = PilotReviewDisposition.APPROVED_DUPLICATE
    second = await service.execute(update=_callback(), now=NOW)
    assert second.disposition is PilotReviewDisposition.APPROVED_DUPLICATE
    assert store.callbacks == 2
    assert telegram.callbacks == ["callback-1", "callback-1"]


@pytest.mark.anyio
async def test_correction_only_accepts_pending_exact_actor_and_chat() -> None:
    store = FakeStore()
    service = ProcessPilotTranscriptReviewUpdate(
        store=store,
        telegram=FakeTelegram(),
        identity_pepper=PEPPER,
        enabled=True,
    )
    accepted = await service.execute(update=_correction(), now=NOW)
    assert accepted.disposition is PilotReviewDisposition.CORRECTION_ACCEPTED
    assert store.corrections == ["Исправленный текст"]
    duplicate = await service.execute(update=_correction(), now=NOW)
    assert duplicate.disposition is PilotReviewDisposition.NOT_APPLICABLE


@pytest.mark.anyio
async def test_correction_from_other_actor_fails_closed() -> None:
    service = ProcessPilotTranscriptReviewUpdate(
        store=FakeStore(),
        telegram=FakeTelegram(),
        identity_pepper=PEPPER,
        enabled=True,
    )
    with pytest.raises(BecomeControlledIntakeError, match="wrong_actor"):
        await service.execute(
            update=_correction(actor_id=99),
            now=NOW,
        )


@pytest.mark.anyio
async def test_kill_switch_blocks_callbacks_and_correction() -> None:
    service = ProcessPilotTranscriptReviewUpdate(
        store=FakeStore(),
        telegram=FakeTelegram(),
        identity_pepper=PEPPER,
        enabled=False,
    )
    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_pilot_slice1_disabled",
    ):
        await service.execute(update=_callback(), now=NOW)


def test_presentation_is_human_readable_and_hides_internal_refs() -> None:
    job = PilotDeliveryJob(
        delivery_id="delivery-private",
        kind="presentation",
        provider_dialog_id="-100",
        version=2,
        transcript_digest="a" * 64,
        callback_ref=TOKEN,
        transcript_text="Человеческий текст",
    )
    text, markup = delivery_body(job)
    assert text == "Вот распознанный текст:\n\nЧеловеческий текст"
    assert markup is not None
    rendered = repr(markup)
    assert "Подтвердить" in rendered
    assert "Исправить" in rendered
    assert "delivery-private" not in text
    assert "a" * 64 not in text
    assert "-100" not in text


def test_owner_redisplay_uses_saved_text_and_same_human_buttons() -> None:
    job = PilotDeliveryJob(
        delivery_id="redisplay-private",
        kind="redisplay",
        provider_dialog_id="-100",
        version=2,
        transcript_digest="b" * 64,
        callback_ref=TOKEN,
        transcript_text="Сохранённый текст",
    )
    text, markup = delivery_body(job)
    assert text == "Вот распознанный текст:\n\nСохранённый текст"
    assert markup is not None
    assert "Подтвердить" in repr(markup)
    assert "Исправить" in repr(markup)

@pytest.mark.anyio
async def test_duplicate_worker_execution_sends_once() -> None:
    store = FakeStore()
    store.jobs.append(
        PilotDeliveryJob(
            delivery_id="d1",
            kind="presentation",
            provider_dialog_id="-100",
            version=1,
            transcript_digest="a" * 64,
            callback_ref=TOKEN,
            transcript_text="Текст",
        )
    )
    telegram = FakeTelegram()
    service = DeliverPilotTranscriptReview(
        store=store,
        telegram=telegram,
        enabled=True,
        max_operations=4,
    )
    assert await service.run_once(now=NOW) == "sent"
    assert await service.run_once(now=NOW) == "idle"
    assert len(telegram.messages) == 1
    assert store.settlements == [("d1", "sent", "11")]


@pytest.mark.anyio
async def test_unknown_delivery_is_terminal_without_blind_retry() -> None:
    store = FakeStore()
    store.jobs.append(
        PilotDeliveryJob(
            delivery_id="d1",
            kind="confirmation",
            provider_dialog_id="-100",
            version=2,
            transcript_digest="a" * 64,
            callback_ref=TOKEN,
        )
    )
    telegram = FakeTelegram()
    telegram.fail = True
    service = DeliverPilotTranscriptReview(
        store=store,
        telegram=telegram,
        enabled=True,
        max_operations=4,
    )
    assert await service.run_once(now=NOW) == "unknown"
    assert await service.run_once(now=NOW) == "idle"
    assert store.settlements == [("d1", "unknown", None)]


@pytest.mark.anyio
async def test_operation_cap_zero_disables_delivery() -> None:
    service = DeliverPilotTranscriptReview(
        store=FakeStore(),
        telegram=FakeTelegram(),
        enabled=True,
        max_operations=0,
    )
    assert await service.run_once(now=NOW) == "disabled"


def test_callback_data_is_bounded_and_exact() -> None:
    assert parse_callback_data(f"b1:c:{TOKEN}") == ("c", TOKEN)
    assert len(f"b1:c:{TOKEN}") <= 64
    with pytest.raises(BecomeControlledIntakeError):
        parse_callback_data(f"b1:c:{TOKEN}:mutated")
