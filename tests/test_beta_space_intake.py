"""Synthetic admission, isolation and idempotency proofs for Beta Spaces."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime

from app.application.beta_space_intake import (
    CONSENT_RESPONSE,
    BetaConsentDeliveryJob,
    BetaSpaceBinding,
    BetaSpaceDisposition,
    BetaSpaceReservation,
    DeliverBetaSpaceConsent,
    ProcessBetaSpaceUpdate,
)

NOW = datetime(2026, 8, 5, 7, 0, tzinfo=UTC)
SYNTHETIC_CONSENT = "Synthetic consent for isolated dental notes"
SPACE_A = BetaSpaceBinding(
    binding_id="binding-a",
    workspace_id="workspace-a",
    tenant_ref="tenant-a",
    space_ref="space-a",
    installation_ref="installation-a",
    telegram_chat_id="-10001",
    subject_telegram_user_id="70001",
    consent_sha256=hashlib.sha256(SYNTHETIC_CONSENT.encode()).hexdigest(),
    state="ready",
)
SPACE_B = replace(
    SPACE_A,
    binding_id="binding-b",
    workspace_id="workspace-b",
    tenant_ref="tenant-b",
    space_ref="space-b",
    installation_ref="installation-b",
    telegram_chat_id="-10002",
    subject_telegram_user_id="70002",
)


class MemoryStore:
    def __init__(self) -> None:
        self.bindings = {SPACE_A.telegram_chat_id: SPACE_A, SPACE_B.telegram_chat_id: SPACE_B}
        self.events: set[tuple[str, int]] = set()
        self.facts: list[tuple[str, str, str, str]] = []
        self.delivery_states = {"delivery-a": "pending"}

    async def resolve_binding(self, *, telegram_chat_id: str):
        return self.bindings.get(telegram_chat_id)

    async def reserve_consent(self, *, binding, source):
        key = (binding.binding_id, source.update_id)
        if key in self.events:
            return BetaSpaceReservation(False)
        self.events.add(key)
        self.bindings[source.telegram_chat_id] = replace(binding, state="consented")
        return BetaSpaceReservation(True, "event-a", "delivery-a")

    async def append_fact(self, *, binding, source):
        key = (binding.binding_id, source.update_id)
        if key in self.events:
            return BetaSpaceReservation(False)
        self.events.add(key)
        self.facts.append(
            (binding.tenant_ref, binding.space_ref, source.telegram_user_id, source.media_kind)
        )
        return BetaSpaceReservation(True, "event-fact")

    async def claim_consent_delivery(self, *, delivery_id: str, now: datetime):
        if self.delivery_states.get(delivery_id) != "pending":
            return None
        self.delivery_states[delivery_id] = "reserved"
        return BetaConsentDeliveryJob(delivery_id, SPACE_A.telegram_chat_id)

    async def settle_consent_delivery(
        self, *, delivery_id, outcome, telegram_message_id, settled_at
    ):
        self.delivery_states[delivery_id] = outcome


class RecordingTelegram:
    def __init__(self, *, confirmed: bool = True) -> None:
        self.confirmed = confirmed
        self.sends: list[tuple[str, str]] = []

    async def send_plain_message(self, *, chat_id: str, text: str):
        self.sends.append((chat_id, text))
        return {"ok": True, "result": {"message_id": 901}} if self.confirmed else {"ok": True}


def update(*, update_id=1, chat_id=-10001, user_id=70001, message_id=11, text=None, kind=None):
    message = {
        "message_id": message_id,
        "date": 1785913200,
        "chat": {"id": chat_id, "type": "supergroup"},
        "from": {"id": user_id, "is_bot": False},
    }
    if text is not None:
        message["text"] = text
    elif kind == "voice":
        message["voice"] = {"file_unique_id": "synthetic-voice"}
    elif kind == "photo":
        message["photo"] = [{"file_unique_id": "synthetic-photo"}]
    elif kind == "document":
        message["document"] = {"file_unique_id": "synthetic-document"}
    return {"update_id": update_id, "message": message}


def run(service, value):
    return asyncio.run(service.execute(update=value, receipt_id="receipt", received_at=NOW))


def test_consent_requires_production_readiness_and_exact_plain_message() -> None:
    store = MemoryStore()
    disabled = ProcessBetaSpaceUpdate(store=store, production_ready=False)
    enabled = ProcessBetaSpaceUpdate(store=store, production_ready=True)

    assert (
        run(disabled, update(text=SYNTHETIC_CONSENT)).disposition
        == BetaSpaceDisposition.DISABLED
    )
    assert (
        run(enabled, update(text=f" {SYNTHETIC_CONSENT}")).disposition
        == BetaSpaceDisposition.CONSENT_REQUIRED
    )
    result = run(enabled, update(text=SYNTHETIC_CONSENT))
    assert result.disposition == BetaSpaceDisposition.CONSENT_ACCEPTED
    assert result.delivery_id == "delivery-a"


def test_negative_routing_never_creates_an_event() -> None:
    store = MemoryStore()
    service = ProcessBetaSpaceUpdate(store=store, production_ready=True)

    assert (
        run(service, update(chat_id=-19999, text=SYNTHETIC_CONSENT)).disposition
        == BetaSpaceDisposition.UNBOUND_CHAT
    )
    assert (
        run(service, update(user_id=79999, text=SYNTHETIC_CONSENT)).disposition
        == BetaSpaceDisposition.ACTOR_MISMATCH
    )
    assert store.events == set()


def test_cross_space_events_keep_exact_tenant_space_and_actor_scope() -> None:
    store = MemoryStore()
    store.bindings[SPACE_A.telegram_chat_id] = replace(SPACE_A, state="consented")
    store.bindings[SPACE_B.telegram_chat_id] = replace(SPACE_B, state="consented")
    service = ProcessBetaSpaceUpdate(store=store, production_ready=True)

    assert (
        run(service, update(update_id=10, kind="voice")).disposition
        == BetaSpaceDisposition.FACT_ACCEPTED
    )
    assert (
        run(
            service,
            update(update_id=11, chat_id=-10002, user_id=70002, kind="document"),
        ).disposition
        == BetaSpaceDisposition.FACT_ACCEPTED
    )
    assert store.facts == [
        ("tenant-a", "space-a", "70001", "voice"),
        ("tenant-b", "space-b", "70002", "document"),
    ]


def test_duplicate_update_creates_no_second_fact_or_delivery() -> None:
    store = MemoryStore()
    service = ProcessBetaSpaceUpdate(store=store, production_ready=True)
    first = run(service, update(update_id=20, text=SYNTHETIC_CONSENT))
    second = run(service, update(update_id=20, text=SYNTHETIC_CONSENT))

    assert first.disposition == BetaSpaceDisposition.CONSENT_ACCEPTED
    assert second.disposition == BetaSpaceDisposition.DUPLICATE
    assert len(store.events) == 1


def test_consent_before_processing_and_other_actor_is_not_attributed() -> None:
    store = MemoryStore()
    service = ProcessBetaSpaceUpdate(store=store, production_ready=True)

    assert (
        run(service, update(update_id=30, kind="photo")).disposition
        == BetaSpaceDisposition.CONSENT_REQUIRED
    )
    run(service, update(update_id=31, text=SYNTHETIC_CONSENT))
    assert (
        run(service, update(update_id=32, user_id=70003, kind="photo")).disposition
        == BetaSpaceDisposition.ACTOR_MISMATCH
    )
    assert (
        run(service, update(update_id=33, kind="photo")).disposition
        == BetaSpaceDisposition.FACT_ACCEPTED
    )
    assert store.facts == [("tenant-a", "space-a", "70001", "photo")]


def test_delivery_is_exactly_once_and_unknown_is_not_retried() -> None:
    store = MemoryStore()
    telegram = RecordingTelegram(confirmed=False)
    delivery = DeliverBetaSpaceConsent(store=store, telegram=telegram, enabled=True)

    assert asyncio.run(delivery.execute(delivery_id="delivery-a", now=NOW)) == "unknown"
    assert asyncio.run(delivery.execute(delivery_id="delivery-a", now=NOW)) == "duplicate"
    assert telegram.sends == [(SPACE_A.telegram_chat_id, CONSENT_RESPONSE)]

