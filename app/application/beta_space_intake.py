"""Fail-closed intake for one isolated Beta Space without model invocation."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

JsonObject = dict[str, Any]

KATYA_CONSENT_TEXT = (
    "Я согласна, чтобы Бета сохраняла и систематизировала новые сообщения "
    "о моих зубах и лечении из этой группы"
)
BETA_CONSENT_CALLBACK_YES = "beta:katya:consent:yes"
BETA_CONSENT_CALLBACK_NO = "beta:katya:consent:no"

CONSENT_RESPONSE = (
    "Катя, согласие получено. Я буду собирать и систематизировать только ту "
    "информацию о зубах и лечении, которую ты размещаешь в этой группе. "
    "Можешь отправлять текст, голосовые сообщения, фотографии и документы. "
    "Сначала я соберу факты, затем покажу тебе структурированную карточку для проверки"
)


class BetaSpaceDisposition(StrEnum):
    DISABLED = "disabled"
    NOT_APPLICABLE = "not_applicable"
    UNBOUND_CHAT = "unbound_chat"
    SPACE_NOT_READY = "space_not_ready"
    PRE_READINESS = "pre_readiness"
    ACTOR_MISMATCH = "actor_mismatch"
    CONSENT_REQUIRED = "consent_required"
    CONSENT_ACCEPTED = "consent_accepted"
    CONSENT_DECLINED = "consent_declined"
    FACT_ACCEPTED = "fact_accepted"
    DUPLICATE = "duplicate"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class BetaSpaceBinding:
    binding_id: str
    workspace_id: str
    connector_id: str
    tenant_ref: str
    space_ref: str
    installation_ref: str
    telegram_chat_id: str = field(repr=False)
    subject_telegram_user_id: str = field(repr=False)
    consent_sha256: str = field(repr=False)
    ready_at: datetime
    state: str
    actor_policy: str = "subject_only"


@dataclass(frozen=True, slots=True)
class BetaSpaceSource:
    receipt_id: str
    update_id: int
    telegram_chat_id: str = field(repr=False)
    telegram_user_id: str = field(repr=False)
    telegram_message_id: str
    occurred_at: datetime
    received_at: datetime
    media_kind: str
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class BetaSpaceReservation:
    created: bool
    event_id: str | None = None
    delivery_id: str | None = None


@dataclass(frozen=True, slots=True)
class BetaSpaceIntakeResult:
    disposition: BetaSpaceDisposition
    tenant_ref: str | None = None
    space_ref: str | None = None
    delivery_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class BetaConsentDeliveryJob:
    delivery_id: str
    telegram_chat_id: str = field(repr=False)


class BetaSpaceStore(Protocol):
    async def resolve_binding(
        self,
        *,
        workspace_key: str,
        connector_key: str,
        telegram_chat_id: str,
    ) -> BetaSpaceBinding | None: ...

    async def reserve_consent(
        self, *, binding: BetaSpaceBinding, source: BetaSpaceSource
    ) -> BetaSpaceReservation: ...

    async def append_fact(
        self, *, binding: BetaSpaceBinding, source: BetaSpaceSource
    ) -> BetaSpaceReservation: ...


class BetaConsentDeliveryStore(Protocol):
    async def claim_consent_delivery(
        self, *, delivery_id: str, now: datetime
    ) -> BetaConsentDeliveryJob | None: ...

    async def settle_consent_delivery(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        settled_at: datetime,
    ) -> None: ...


class BetaTelegram(Protocol):
    async def send_plain_message(self, *, chat_id: str, text: str) -> JsonObject: ...


class ProcessBetaSpaceUpdate:
    """Route an ordinary Beta group message into exactly one bound Space."""

    def __init__(
        self,
        *,
        store: BetaSpaceStore,
        production_ready: bool,
        workspace_key: str,
        connector_key: str,
    ) -> None:
        self._store = store
        self._production_ready = production_ready
        self._workspace_key = workspace_key
        self._connector_key = connector_key

    async def execute(
        self, *, update: JsonObject, receipt_id: str, received_at: datetime
    ) -> BetaSpaceIntakeResult:
        if not self._production_ready:
            return BetaSpaceIntakeResult(BetaSpaceDisposition.DISABLED)
        callback = extract_beta_consent_callback_source(
            update=update, receipt_id=receipt_id, received_at=received_at
        )
        source_and_text = extract_beta_space_source(
            update=update, receipt_id=receipt_id, received_at=received_at
        )
        if callback is None and source_and_text is None:
            return BetaSpaceIntakeResult(BetaSpaceDisposition.NOT_APPLICABLE)
        if callback is not None:
            source, consent_decision = callback
            text = None
        else:
            assert source_and_text is not None
            source, text = source_and_text
            consent_decision = None
        binding = await self._store.resolve_binding(
            workspace_key=self._workspace_key,
            connector_key=self._connector_key,
            telegram_chat_id=source.telegram_chat_id
        )
        if binding is None:
            return BetaSpaceIntakeResult(BetaSpaceDisposition.UNBOUND_CHAT)
        scope = {"tenant_ref": binding.tenant_ref, "space_ref": binding.space_ref}
        if binding.state not in {"ready", "consented"}:
            return BetaSpaceIntakeResult(BetaSpaceDisposition.SPACE_NOT_READY, **scope)
        # The production-ready instant is inclusive: an update created exactly at
        # ready_at is eligible, while a delayed update created before it is stale.
        if binding.ready_at.tzinfo is None or source.occurred_at < binding.ready_at:
            return BetaSpaceIntakeResult(BetaSpaceDisposition.PRE_READINESS, **scope)
        if binding.actor_policy not in {"subject_only", "group_members"}:
            return BetaSpaceIntakeResult(BetaSpaceDisposition.ACTOR_MISMATCH, **scope)
        if binding.actor_policy == "subject_only" and not hmac.compare_digest(
            source.telegram_user_id.encode(),
            binding.subject_telegram_user_id.encode(),
        ):
            return BetaSpaceIntakeResult(BetaSpaceDisposition.ACTOR_MISMATCH, **scope)

        exact_consent = text is not None and hmac.compare_digest(
            hashlib.sha256(text.encode()).hexdigest(), binding.consent_sha256
        )
        if binding.state == "ready":
            if consent_decision == "no":
                return BetaSpaceIntakeResult(BetaSpaceDisposition.CONSENT_DECLINED, **scope)
            if consent_decision != "yes":
                return BetaSpaceIntakeResult(BetaSpaceDisposition.CONSENT_REQUIRED, **scope)
            reservation = await self._store.reserve_consent(binding=binding, source=source)
            if not reservation.created:
                return BetaSpaceIntakeResult(BetaSpaceDisposition.DUPLICATE, **scope)
            return BetaSpaceIntakeResult(
                BetaSpaceDisposition.CONSENT_ACCEPTED,
                delivery_id=reservation.delivery_id,
                **scope,
            )

        if consent_decision is not None or exact_consent:
            return BetaSpaceIntakeResult(BetaSpaceDisposition.DUPLICATE, **scope)
        if source.media_kind not in {"text", "voice", "photo", "document"}:
            return BetaSpaceIntakeResult(BetaSpaceDisposition.UNSUPPORTED, **scope)
        reservation = await self._store.append_fact(binding=binding, source=source)
        return BetaSpaceIntakeResult(
            BetaSpaceDisposition.FACT_ACCEPTED
            if reservation.created
            else BetaSpaceDisposition.DUPLICATE,
            **scope,
        )


class DeliverBetaSpaceConsent:
    """Send one reserved deterministic consent response, never retrying ambiguity."""

    def __init__(
        self,
        *,
        store: BetaConsentDeliveryStore,
        telegram: BetaTelegram,
        enabled: bool,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._enabled = enabled

    async def execute(self, *, delivery_id: str, now: datetime) -> str:
        if not self._enabled:
            return "disabled"
        job = await self._store.claim_consent_delivery(delivery_id=delivery_id, now=now)
        if job is None:
            return "duplicate"
        try:
            response = await self._telegram.send_plain_message(
                chat_id=job.telegram_chat_id,
                text=CONSENT_RESPONSE,
            )
            message_id = response.get("result", {}).get("message_id")
            if type(message_id) is not int:
                raise RuntimeError("beta_consent_delivery_unconfirmed")
        except Exception:
            await self._store.settle_consent_delivery(
                delivery_id=delivery_id,
                outcome="unknown",
                telegram_message_id=None,
                settled_at=now,
            )
            return "unknown"
        await self._store.settle_consent_delivery(
            delivery_id=delivery_id,
            outcome="sent",
            telegram_message_id=str(message_id),
            settled_at=now,
        )
        return "sent"


def extract_beta_space_source(
    *, update: JsonObject, receipt_id: str, received_at: datetime
) -> tuple[BetaSpaceSource, str | None] | None:
    update_id = update.get("update_id")
    message = update.get("message")
    if type(update_id) is not int or not isinstance(message, dict):
        return None
    chat = message.get("chat")
    actor = message.get("from")
    message_id = message.get("message_id")
    occurred = message.get("date")
    if (
        not isinstance(chat, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or type(chat.get("id")) is not int
        or not isinstance(actor, dict)
        or type(actor.get("id")) is not int
        or actor.get("is_bot") is not False
        or type(message_id) is not int
        or type(occurred) is not int
    ):
        return None
    text = message.get("text") if isinstance(message.get("text"), str) else None
    if text is not None:
        media_kind = "text"
    elif isinstance(message.get("voice"), dict):
        media_kind = "voice"
    elif isinstance(message.get("photo"), list) and message["photo"]:
        media_kind = "photo"
    elif isinstance(message.get("document"), dict):
        media_kind = "document"
    else:
        media_kind = "unsupported"
    payload = json.dumps(
        update,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return (
        BetaSpaceSource(
            receipt_id=receipt_id,
            update_id=update_id,
            telegram_chat_id=str(chat["id"]),
            telegram_user_id=str(actor["id"]),
            telegram_message_id=str(message_id),
            occurred_at=datetime.fromtimestamp(occurred, UTC),
            received_at=received_at,
            media_kind=media_kind,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        ),
        text,
    )


def extract_beta_consent_callback_source(
    *, update: JsonObject, receipt_id: str, received_at: datetime
) -> tuple[BetaSpaceSource, str] | None:
    """Extract only the two exact inline-consent actions from one group callback."""

    update_id = update.get("update_id")
    callback = update.get("callback_query")
    if type(update_id) is not int or not isinstance(callback, dict):
        return None
    callback_id = callback.get("id")
    data = callback.get("data")
    actor = callback.get("from")
    message = callback.get("message")
    if (
        not isinstance(callback_id, str)
        or not callback_id
        or data not in {BETA_CONSENT_CALLBACK_YES, BETA_CONSENT_CALLBACK_NO}
        or not isinstance(actor, dict)
        or type(actor.get("id")) is not int
        or actor.get("is_bot") is not False
        or not isinstance(message, dict)
    ):
        return None
    chat = message.get("chat")
    message_id = message.get("message_id")
    if (
        not isinstance(chat, dict)
        or chat.get("type") not in {"group", "supergroup"}
        or type(chat.get("id")) is not int
        or type(message_id) is not int
    ):
        return None
    payload = json.dumps(
        update,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return (
        BetaSpaceSource(
            receipt_id=receipt_id,
            update_id=update_id,
            telegram_chat_id=str(chat["id"]),
            telegram_user_id=str(actor["id"]),
            telegram_message_id=str(message_id),
            occurred_at=received_at,
            received_at=received_at,
            media_kind="callback",
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        ),
        "yes" if data == BETA_CONSENT_CALLBACK_YES else "no",
    )
