"""Synthetic, provider-neutral bounded-read contract for personal Telegram."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.application.personal_telegram_transport import (
    OPAQUE_REF_CHARACTERS,
    PersonalTelegramPolicy,
    PersonalTelegramTransportError,
)

MAX_READ_MESSAGES = 5
MAX_READ_WINDOW = timedelta(hours=24)


class ReadOutcome(StrEnum):
    OBSERVED = "observed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


@dataclass(frozen=True, slots=True)
class PersonalTelegramMetadataEnvelope:
    """Content-free synthetic cloud-message metadata."""

    event_ref: str
    occurred_at: datetime
    direction: MessageDirection


@dataclass(frozen=True, slots=True)
class ProviderReadReceipt:
    outcome: ReadOutcome
    envelopes: tuple[PersonalTelegramMetadataEnvelope, ...]
    provider_state_mutated: bool | None


@dataclass(frozen=True, slots=True)
class ReadMetadataCommand:
    actor_ref: str
    session_ref: str
    peer_ref: str
    grant_token: str
    idempotency_key: str
    not_before: datetime
    limit: int
    now: datetime


@dataclass(frozen=True, slots=True)
class ReadMetadataResult:
    outcome: ReadOutcome
    envelope_count: int
    envelope_digest: str | None
    window_started_at: datetime
    window_ended_at: datetime
    idempotent_replay: bool
    provider_state_mutated: bool | None
    read_capability_active: bool


class PersonalTelegramReadTransport(Protocol):
    """Provider-neutral metadata-only port; no production adapter exists."""

    async def read_recent_metadata(
        self,
        *,
        session_ref: str,
        peer_ref: str,
        not_before: datetime,
        limit: int,
    ) -> ProviderReadReceipt:
        """Return bounded metadata without marking messages read."""
        ...


@dataclass(slots=True)
class _ReadGrant:
    token_digest: str
    actor_ref: str
    session_ref: str
    peer_ref: str
    not_before: datetime
    max_messages: int
    expires_at: datetime
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class _ReadReplay:
    fingerprint: str
    result: ReadMetadataResult


class MemoryPersonalTelegramReadControlStore:
    """Digest-only one-use grants and redacted replay receipts."""

    def __init__(self) -> None:
        self._grants: dict[str, _ReadGrant] = {}
        self._replays: dict[str, _ReadReplay] = {}

    @property
    def grant_token_digests(self) -> frozenset[str]:
        return frozenset(self._grants)

    def register_grant(
        self,
        *,
        grant_token: str,
        actor_ref: str,
        session_ref: str,
        peer_ref: str,
        not_before: datetime,
        max_messages: int,
        expires_at: datetime,
    ) -> str:
        token_digest = _secret_digest(grant_token)
        if token_digest in self._grants:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_grant_duplicate"
            )
        _require_aware(not_before, "not_before")
        _require_aware(expires_at, "expiry")
        _require_limit(max_messages)
        if expires_at <= not_before:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_grant_window_invalid"
            )
        self._grants[token_digest] = _ReadGrant(
            token_digest=token_digest,
            actor_ref=_require_prefixed_ref(actor_ref, "actor_", "actor_ref"),
            session_ref=_require_prefixed_ref(
                session_ref,
                "session_",
                "session_ref",
            ),
            peer_ref=_require_prefixed_ref(peer_ref, "peer_", "peer_ref"),
            not_before=not_before,
            max_messages=max_messages,
            expires_at=expires_at,
        )
        return token_digest

    def reserve(
        self,
        *,
        grant_token: str,
        actor_ref: str,
        session_ref: str,
        peer_ref: str,
        not_before: datetime,
        limit: int,
        now: datetime,
    ) -> None:
        token_digest = _secret_digest(grant_token)
        grant = self._grants.get(token_digest)
        if grant is None:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_grant_unknown"
            )
        if grant.consumed:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_grant_consumed"
            )
        if grant.expires_at <= now:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_grant_expired"
            )
        expected = (
            grant.actor_ref,
            grant.session_ref,
            grant.peer_ref,
            grant.not_before,
        )
        actual = (actor_ref, session_ref, peer_ref, not_before)
        if actual != expected or limit > grant.max_messages:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_scope_mismatch"
            )
        grant.consumed = True

    def replay(self, idempotency_key: str) -> _ReadReplay | None:
        return self._replays.get(idempotency_key)

    def remember(self, idempotency_key: str, replay: _ReadReplay) -> None:
        if idempotency_key in self._replays:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_replay_race"
            )
        self._replays[idempotency_key] = replay


class ReadPersonalTelegramMetadataService:
    """Execute one bounded metadata read and retain only a redacted result."""

    def __init__(
        self,
        *,
        policy: PersonalTelegramPolicy,
        store: MemoryPersonalTelegramReadControlStore,
        transport: PersonalTelegramReadTransport,
    ) -> None:
        self._policy = policy
        self._store = store
        self._transport = transport

    async def execute(self, command: ReadMetadataCommand) -> ReadMetadataResult:
        if not self._policy.read_dialogs_enabled:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_disabled"
            )
        actor_ref = _require_prefixed_ref(
            command.actor_ref,
            "actor_",
            "actor_ref",
        )
        session_ref = _require_prefixed_ref(
            command.session_ref,
            "session_",
            "session_ref",
        )
        peer_ref = _require_prefixed_ref(
            command.peer_ref,
            "peer_",
            "peer_ref",
        )
        idempotency_key = _require_prefixed_ref(
            command.idempotency_key,
            "read_",
            "idempotency_key",
        )
        _require_aware(command.not_before, "not_before")
        _require_aware(command.now, "now")
        _require_limit(command.limit)
        if (
            command.not_before >= command.now
            or command.now - command.not_before > MAX_READ_WINDOW
        ):
            raise PersonalTelegramTransportError(
                "personal_telegram_read_window_invalid"
            )

        fingerprint = _digest(
            {
                "actor_ref": actor_ref,
                "session_ref": session_ref,
                "peer_ref": peer_ref,
                "idempotency_key": idempotency_key,
                "not_before": command.not_before.isoformat(),
                "limit": command.limit,
            }
        )
        previous = self._store.replay(idempotency_key)
        if previous is not None:
            if previous.fingerprint != fingerprint:
                raise PersonalTelegramTransportError(
                    "personal_telegram_read_idempotency_conflict"
                )
            return replace(previous.result, idempotent_replay=True)

        self._store.reserve(
            grant_token=command.grant_token,
            actor_ref=actor_ref,
            session_ref=session_ref,
            peer_ref=peer_ref,
            not_before=command.not_before,
            limit=command.limit,
            now=command.now,
        )

        try:
            provider_receipt = await self._transport.read_recent_metadata(
                session_ref=session_ref,
                peer_ref=peer_ref,
                not_before=command.not_before,
                limit=command.limit,
            )
            result = _redact_provider_receipt(
                provider_receipt,
                not_before=command.not_before,
                now=command.now,
                limit=command.limit,
            )
        except Exception:
            result = ReadMetadataResult(
                outcome=ReadOutcome.UNKNOWN,
                envelope_count=0,
                envelope_digest=None,
                window_started_at=command.not_before,
                window_ended_at=command.now,
                idempotent_replay=False,
                provider_state_mutated=None,
                read_capability_active=False,
            )

        self._store.remember(
            idempotency_key,
            _ReadReplay(fingerprint=fingerprint, result=result),
        )
        return result


class FakePersonalTelegramReadTransport:
    """Synthetic adapter with no account, network, content or external effect."""

    def __init__(
        self,
        *,
        envelopes: tuple[PersonalTelegramMetadataEnvelope, ...] = (),
        outcome: ReadOutcome = ReadOutcome.OBSERVED,
        raise_unknown: bool = False,
        provider_state_mutated: bool = False,
    ) -> None:
        self._envelopes = envelopes
        self._outcome = outcome
        self._raise_unknown = raise_unknown
        self._provider_state_mutated = provider_state_mutated
        self.attempts: list[tuple[str, str, datetime, int]] = []

    async def read_recent_metadata(
        self,
        *,
        session_ref: str,
        peer_ref: str,
        not_before: datetime,
        limit: int,
    ) -> ProviderReadReceipt:
        self.attempts.append((session_ref, peer_ref, not_before, limit))
        if self._raise_unknown:
            raise TimeoutError("synthetic_personal_telegram_read_unknown")
        return ProviderReadReceipt(
            outcome=self._outcome,
            envelopes=self._envelopes,
            provider_state_mutated=self._provider_state_mutated,
        )


def _redact_provider_receipt(
    receipt: ProviderReadReceipt,
    *,
    not_before: datetime,
    now: datetime,
    limit: int,
) -> ReadMetadataResult:
    if receipt.provider_state_mutated is not False:
        raise PersonalTelegramTransportError(
            "personal_telegram_read_provider_mutation"
        )
    if len(receipt.envelopes) > limit:
        raise PersonalTelegramTransportError(
            "personal_telegram_read_provider_limit"
        )
    if receipt.outcome is not ReadOutcome.OBSERVED and receipt.envelopes:
        raise PersonalTelegramTransportError(
            "personal_telegram_read_provider_contract"
        )

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for envelope in receipt.envelopes:
        event_ref = _require_prefixed_ref(
            envelope.event_ref,
            "event_",
            "event_ref",
        )
        _require_aware(envelope.occurred_at, "event_time")
        if not not_before <= envelope.occurred_at <= now:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_provider_window"
            )
        if event_ref in seen:
            raise PersonalTelegramTransportError(
                "personal_telegram_read_provider_duplicate"
            )
        seen.add(event_ref)
        normalized.append(
            {
                "event_ref": event_ref,
                "occurred_at": envelope.occurred_at.isoformat(),
                "direction": envelope.direction.value,
            }
        )

    return ReadMetadataResult(
        outcome=receipt.outcome,
        envelope_count=len(normalized),
        envelope_digest=_digest(normalized) if normalized else None,
        window_started_at=not_before,
        window_ended_at=now,
        idempotent_replay=False,
        provider_state_mutated=False,
        read_capability_active=False,
    )


def _require_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_READ_MESSAGES:
        raise PersonalTelegramTransportError(
            "personal_telegram_read_limit_invalid"
        )
    return value


def _require_aware(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PersonalTelegramTransportError(
            f"personal_telegram_read_{field_name}_invalid"
        )
    return value


def _secret_digest(value: str) -> str:
    if not isinstance(value, str) or len(value) < 24 or value != value.strip():
        raise PersonalTelegramTransportError(
            "personal_telegram_read_grant_token_invalid"
        )
    return hashlib.sha256(value.encode()).hexdigest()


def _require_opaque_ref(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not 3 <= len(value) <= 128
        or value != value.strip()
        or any(character not in OPAQUE_REF_CHARACTERS for character in value)
    ):
        raise PersonalTelegramTransportError(
            f"personal_telegram_read_{field_name}_invalid"
        )
    return value


def _require_prefixed_ref(value: object, prefix: str, field_name: str) -> str:
    reference = _require_opaque_ref(value, field_name)
    if not reference.startswith(prefix):
        raise PersonalTelegramTransportError(
            f"personal_telegram_read_{field_name}_invalid"
        )
    return reference


def _digest(value: object) -> str:
    wire = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(wire.encode()).hexdigest()
