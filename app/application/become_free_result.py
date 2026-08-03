"""One private, consent-bound BECOME free result and Telegram delivery."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)

FREE_RESULT_SCHEMA = "gate.become-free-result@1"
FREE_RESULT_DELIVERY_SCHEMA = "gate.become-free-result-delivery@1"
FREE_RESULT_PREFIX = (
    "Ваш бесплатный результат BECOME готов.\n\n"
    "Мы услышали ваш ответ так:\n\n"
)
FREE_RESULT_SUFFIX = (
    "\n\nЭто сохранённая версия вашего ответа. "
    "Если всё верно, следующим отдельным шагом можно будет продолжить."
)
MAX_FREE_RESULT_LENGTH = 4096

_ARTIFACT_FIELDS = frozenset(
    {
        "schema",
        "correlation_ref",
        "attempt_version",
        "take_receipt_digest",
        "transcript_digest",
        "result_digest",
        "external_effects",
    }
)
_DELIVERY_FIELDS = frozenset(
    {
        "schema",
        "correlation_ref",
        "result_digest",
        "take_receipt_digest",
        "destination_binding_digest",
        "classification",
        "external_effects",
        "reconciliation_required",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "chat_id",
        "dialog_id",
        "message_id",
        "provider_id",
        "provider_message_id",
        "telegram_id",
        "text",
        "transcript",
        "transcript_text",
    }
)


JsonObject = dict[str, object]


class BecomeFreeResultState(StrEnum):
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BecomeFreeResultCandidate:
    candidate_key: str
    correlation_ref: str
    result_digest: str
    take_receipt_digest: str
    destination_binding_digest: str
    installation_ref: str
    target_chat_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BecomeFreeResultReservation:
    attempt_key: str
    state: BecomeFreeResultState
    target_chat_id: str | None = field(default=None, repr=False)
    result_text: str | None = field(default=None, repr=False)
    provider_message_id: str | None = field(default=None, repr=False)
    error_code: str | None = None
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class BecomeFreeResultDeliveryResult:
    disposition: str
    state: BecomeFreeResultState | None = None
    sent: bool = False
    duplicate: bool = False
    reconciliation_required: bool = False
    error_code: str | None = None


class BecomeFreeResultStore(Protocol):
    async def candidate(
        self,
        *,
        workspace_key: str,
    ) -> BecomeFreeResultCandidate | None: ...

    async def reserve(
        self,
        *,
        workspace_key: str,
        candidate_key: str,
        now: datetime,
    ) -> BecomeFreeResultReservation: ...

    async def finalize(
        self,
        *,
        workspace_key: str,
        attempt_key: str,
        state: BecomeFreeResultState,
        finished_at: datetime,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> BecomeFreeResultReservation: ...


class BecomeFreeResultPresence(Protocol):
    async def member_count(
        self,
        *,
        installation_ref: str,
        provider_dialog_id: str,
    ) -> int | None: ...


class BecomeFreeResultTelegram(Protocol):
    async def send_text(self, *, chat_id: str, text: str) -> str: ...


class BecomeFreeResultProviderError(RuntimeError):
    def __init__(
        self,
        *,
        state: BecomeFreeResultState,
        error_code: str,
    ) -> None:
        self.state = state
        self.error_code = error_code
        super().__init__(error_code)


class DeliverBecomeFreeResultService:
    """Reserve and send at most one exact private result message."""

    def __init__(
        self,
        *,
        store: BecomeFreeResultStore,
        presence: BecomeFreeResultPresence,
        telegram: BecomeFreeResultTelegram,
        enabled: bool,
        max_calls: int,
    ) -> None:
        if max_calls not in {0, 1}:
            raise ValueError("become_free_result_max_calls_invalid")
        self._store = store
        self._presence = presence
        self._telegram = telegram
        self._enabled = enabled
        self._max_calls = max_calls

    async def execute(
        self,
        *,
        workspace_key: str,
        now: datetime,
    ) -> BecomeFreeResultDeliveryResult:
        if not self._enabled or self._max_calls == 0:
            return BecomeFreeResultDeliveryResult(disposition="disabled")
        if now.tzinfo is None:
            raise BecomeControlledIntakeError(
                "become_free_result_timestamp_invalid"
            )
        candidate = await self._store.candidate(workspace_key=workspace_key)
        if candidate is None:
            return BecomeFreeResultDeliveryResult(disposition="not_ready")
        member_count = await self._presence.member_count(
            installation_ref=candidate.installation_ref,
            provider_dialog_id=candidate.target_chat_id,
        )
        if member_count != 2:
            raise BecomeControlledIntakeError(
                "become_free_result_owner_only_required"
            )
        reservation = await self._store.reserve(
            workspace_key=workspace_key,
            candidate_key=candidate.candidate_key,
            now=now,
        )
        if reservation.state is not BecomeFreeResultState.SENDING:
            return BecomeFreeResultDeliveryResult(
                disposition=reservation.state.value,
                state=reservation.state,
                sent=reservation.state is BecomeFreeResultState.SENT,
                duplicate=reservation.idempotent_replay,
                reconciliation_required=(
                    reservation.state is BecomeFreeResultState.UNKNOWN
                ),
                error_code=reservation.error_code,
            )
        if (
            reservation.target_chat_id is None
            or reservation.result_text is None
            or result_digest(reservation.result_text)
            != candidate.result_digest
        ):
            raise BecomeControlledIntakeError(
                "become_free_result_reservation_invalid"
            )
        try:
            provider_message_id = await self._telegram.send_text(
                chat_id=reservation.target_chat_id,
                text=reservation.result_text,
            )
        except BecomeFreeResultProviderError as exc:
            terminal = await self._store.finalize(
                workspace_key=workspace_key,
                attempt_key=reservation.attempt_key,
                state=exc.state,
                finished_at=now,
                error_code=exc.error_code,
            )
        else:
            terminal = await self._store.finalize(
                workspace_key=workspace_key,
                attempt_key=reservation.attempt_key,
                state=BecomeFreeResultState.SENT,
                finished_at=now,
                provider_message_id=provider_message_id,
            )
        return BecomeFreeResultDeliveryResult(
            disposition=terminal.state.value,
            state=terminal.state,
            sent=terminal.state is BecomeFreeResultState.SENT,
            duplicate=terminal.idempotent_replay,
            reconciliation_required=(
                terminal.state is BecomeFreeResultState.UNKNOWN
            ),
            error_code=terminal.error_code,
        )


def build_free_result_text(transcript: str) -> str:
    normalized = unicodedata.normalize("NFC", transcript).strip()
    if not normalized:
        raise ValueError("become_free_result_transcript_empty")
    result = FREE_RESULT_PREFIX + normalized + FREE_RESULT_SUFFIX
    if len(result) > MAX_FREE_RESULT_LENGTH:
        raise ValueError("become_free_result_single_message_limit_exceeded")
    return result


def result_digest(result_text: str) -> str:
    if not result_text or len(result_text) > MAX_FREE_RESULT_LENGTH:
        raise ValueError("become_free_result_text_invalid")
    return hashlib.sha256(result_text.encode("utf-8")).hexdigest()


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_artifact_receipt(
    *,
    correlation_ref: str,
    take_receipt_digest: str,
    transcript_digest: str,
    exact_result_digest: str,
) -> JsonObject:
    payload: JsonObject = {
        "schema": FREE_RESULT_SCHEMA,
        "correlation_ref": correlation_ref,
        "attempt_version": 2,
        "take_receipt_digest": take_receipt_digest,
        "transcript_digest": transcript_digest,
        "result_digest": exact_result_digest,
        "external_effects": 0,
    }
    return validate_artifact_receipt(payload)


def validate_artifact_receipt(value: object) -> JsonObject:
    payload = _exact_object(value, _ARTIFACT_FIELDS)
    _reject_sensitive(payload)
    if (
        payload["schema"] != FREE_RESULT_SCHEMA
        or payload["attempt_version"] != 2
        or payload["external_effects"] != 0
    ):
        raise ValueError("become_free_result_artifact_invalid")
    _require_correlation(payload["correlation_ref"])
    for field in (
        "take_receipt_digest",
        "transcript_digest",
        "result_digest",
    ):
        _require_digest(payload[field], field)
    return payload


def build_delivery_receipt(
    *,
    correlation_ref: str,
    exact_result_digest: str,
    take_receipt_digest: str,
    destination_binding_digest: str,
    state: BecomeFreeResultState,
) -> JsonObject:
    payload: JsonObject = {
        "schema": FREE_RESULT_DELIVERY_SCHEMA,
        "correlation_ref": correlation_ref,
        "result_digest": exact_result_digest,
        "take_receipt_digest": take_receipt_digest,
        "destination_binding_digest": destination_binding_digest,
        "classification": state.value,
        "external_effects": (
            1 if state is BecomeFreeResultState.SENT else 0
        ),
        "reconciliation_required": (
            state is BecomeFreeResultState.UNKNOWN
        ),
    }
    return validate_delivery_receipt(payload)


def validate_delivery_receipt(value: object) -> JsonObject:
    payload = _exact_object(value, _DELIVERY_FIELDS)
    _reject_sensitive(payload)
    if payload["schema"] != FREE_RESULT_DELIVERY_SCHEMA:
        raise ValueError("become_free_result_delivery_schema_invalid")
    _require_correlation(payload["correlation_ref"])
    for field in (
        "result_digest",
        "take_receipt_digest",
        "destination_binding_digest",
    ):
        _require_digest(payload[field], field)
    try:
        state = BecomeFreeResultState(str(payload["classification"]))
    except ValueError as exc:
        raise ValueError(
            "become_free_result_delivery_state_invalid"
        ) from exc
    if state is BecomeFreeResultState.SENDING:
        raise ValueError("become_free_result_terminal_receipt_required")
    if payload["external_effects"] != (
        1 if state is BecomeFreeResultState.SENT else 0
    ) or payload["reconciliation_required"] is not (
        state is BecomeFreeResultState.UNKNOWN
    ):
        raise ValueError("become_free_result_delivery_outcome_invalid")
    return payload


def validate_receipt_replay(
    value: object,
    *,
    expected_digest: str,
    delivery: bool = False,
) -> JsonObject:
    payload = (
        validate_delivery_receipt(value)
        if delivery
        else validate_artifact_receipt(value)
    )
    if canonical_digest(payload) != expected_digest:
        raise ValueError("become_free_result_mutated_replay")
    return payload


def _exact_object(value: object, fields: frozenset[str]) -> JsonObject:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("become_free_result_receipt_fields_invalid")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("become_free_result_receipt_keys_invalid")
    return dict(value)


def _reject_sensitive(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = unicodedata.normalize("NFKC", str(key)).casefold()
            if normalized in _SENSITIVE_KEYS:
                raise ValueError("become_free_result_sensitive_field")
            _reject_sensitive(nested)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive(item)


def _require_digest(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"become_free_result_{field}_invalid")
    return value


def _require_correlation(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("synthetic://correlation/voice/")
    ):
        raise ValueError("become_free_result_correlation_invalid")
    return value
