"""Default-off personal Telegram user-session transport boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

OPAQUE_REF_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
)
MAX_EXACT_TEXT_LENGTH = 4096


class PersonalTelegramTransportError(RuntimeError):
    """A fail-closed refusal from the personal Telegram boundary."""


class ProviderOutcome(StrEnum):
    SENT = "sent"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PersonalTelegramPolicy:
    """All user-session capabilities remain disabled unless separately activated."""

    read_dialogs_enabled: bool = False
    contact_sync_enabled: bool = False
    send_enabled: bool = False


@dataclass(frozen=True, slots=True)
class ExactSendCommand:
    actor_ref: str
    session_ref: str
    peer_ref: str
    exact_text: str
    approval_token: str
    idempotency_key: str
    now: datetime


@dataclass(frozen=True, slots=True)
class ProviderSendReceipt:
    outcome: ProviderOutcome
    provider_receipt_ref: str | None
    external_effect_performed: bool


@dataclass(frozen=True, slots=True)
class ExactSendResult:
    outcome: ProviderOutcome
    provider_receipt_ref: str | None
    deterministic_random_id: int
    idempotent_replay: bool
    external_effect_performed: bool


class EncryptedSessionSecretStore(Protocol):
    """Secret-store port; implementations return ciphertext, never a session string."""

    def load_ciphertext(self, *, session_ref: str) -> bytes:
        """Load an encrypted session blob by opaque reference."""


class PersonalTelegramTransport(Protocol):
    """Narrow provider port owned by Gate."""

    async def send_exact(
        self,
        *,
        session_ref: str,
        peer_ref: str,
        exact_text: str,
        deterministic_random_id: int,
    ) -> ProviderSendReceipt:
        """Make one exact-text provider attempt."""


@dataclass(slots=True)
class _ApprovalRecord:
    token_digest: str
    actor_ref: str
    peer_ref: str
    exact_text_digest: str
    expires_at: datetime
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class _ReplayRecord:
    fingerprint: str
    result: ExactSendResult


class MemoryPersonalTelegramControlStore:
    """Digest-only approval and replay store for synthetic contract tests."""

    def __init__(self) -> None:
        self._approvals: dict[str, _ApprovalRecord] = {}
        self._replays: dict[str, _ReplayRecord] = {}

    @property
    def approval_token_digests(self) -> frozenset[str]:
        return frozenset(self._approvals)

    def register_exact_approval(
        self,
        *,
        approval_token: str,
        actor_ref: str,
        peer_ref: str,
        exact_text: str,
        expires_at: datetime,
    ) -> str:
        token_digest = _secret_digest(approval_token)
        if token_digest in self._approvals:
            raise PersonalTelegramTransportError("personal_telegram_approval_duplicate")
        if expires_at.tzinfo is None:
            raise PersonalTelegramTransportError("personal_telegram_expiry_invalid")
        record = _ApprovalRecord(
            token_digest=token_digest,
            actor_ref=_require_opaque_ref(actor_ref, "actor_ref"),
            peer_ref=_require_opaque_ref(peer_ref, "peer_ref"),
            exact_text_digest=_exact_text_digest(exact_text),
            expires_at=expires_at,
        )
        self._approvals[token_digest] = record
        return token_digest

    def reserve_exact_approval(
        self,
        *,
        approval_token: str,
        actor_ref: str,
        peer_ref: str,
        exact_text: str,
        now: datetime,
    ) -> None:
        if now.tzinfo is None:
            raise PersonalTelegramTransportError("personal_telegram_now_invalid")
        token_digest = _secret_digest(approval_token)
        record = self._approvals.get(token_digest)
        if record is None:
            raise PersonalTelegramTransportError("personal_telegram_approval_unknown")
        if record.consumed:
            raise PersonalTelegramTransportError("personal_telegram_approval_consumed")
        if record.expires_at <= now:
            raise PersonalTelegramTransportError("personal_telegram_approval_expired")
        if record.actor_ref != actor_ref:
            raise PersonalTelegramTransportError("personal_telegram_actor_mismatch")
        if record.peer_ref != peer_ref:
            raise PersonalTelegramTransportError("personal_telegram_peer_mismatch")
        if record.exact_text_digest != _exact_text_digest(exact_text):
            raise PersonalTelegramTransportError("personal_telegram_exact_text_mismatch")
        record.consumed = True

    def replay(self, idempotency_key: str) -> _ReplayRecord | None:
        return self._replays.get(idempotency_key)

    def remember(self, idempotency_key: str, record: _ReplayRecord) -> None:
        if idempotency_key in self._replays:
            raise PersonalTelegramTransportError("personal_telegram_replay_race")
        self._replays[idempotency_key] = record


class SendExactPersonalTelegramService:
    """Authorize one exact synthetic/provider attempt without blind retries."""

    def __init__(
        self,
        *,
        policy: PersonalTelegramPolicy,
        store: MemoryPersonalTelegramControlStore,
        transport: PersonalTelegramTransport,
    ) -> None:
        self._policy = policy
        self._store = store
        self._transport = transport

    async def execute(self, command: ExactSendCommand) -> ExactSendResult:
        if not self._policy.send_enabled:
            raise PersonalTelegramTransportError("personal_telegram_send_disabled")
        actor_ref = _require_opaque_ref(command.actor_ref, "actor_ref")
        session_ref = _require_opaque_ref(command.session_ref, "session_ref")
        peer_ref = _require_opaque_ref(command.peer_ref, "peer_ref")
        idempotency_key = _require_opaque_ref(command.idempotency_key, "idempotency_key")
        text_digest = _exact_text_digest(command.exact_text)
        fingerprint = _digest(
            {
                "actor_ref": actor_ref,
                "session_ref": session_ref,
                "peer_ref": peer_ref,
                "exact_text_digest": text_digest,
                "idempotency_key": idempotency_key,
            }
        )
        previous = self._store.replay(idempotency_key)
        if previous is not None:
            if previous.fingerprint != fingerprint:
                raise PersonalTelegramTransportError(
                    "personal_telegram_idempotency_conflict"
                )
            return replace(previous.result, idempotent_replay=True)

        self._store.reserve_exact_approval(
            approval_token=command.approval_token,
            actor_ref=actor_ref,
            peer_ref=peer_ref,
            exact_text=command.exact_text,
            now=command.now,
        )
        deterministic_random_id = _deterministic_random_id(
            idempotency_key=idempotency_key,
            peer_ref=peer_ref,
            exact_text_digest=text_digest,
        )
        try:
            provider_receipt = await self._transport.send_exact(
                session_ref=session_ref,
                peer_ref=peer_ref,
                exact_text=command.exact_text,
                deterministic_random_id=deterministic_random_id,
            )
        except Exception:
            provider_receipt = ProviderSendReceipt(
                outcome=ProviderOutcome.UNKNOWN,
                provider_receipt_ref=None,
                external_effect_performed=False,
            )

        if provider_receipt.provider_receipt_ref is not None:
            _require_opaque_ref(
                provider_receipt.provider_receipt_ref,
                "provider_receipt_ref",
            )
        result = ExactSendResult(
            outcome=provider_receipt.outcome,
            provider_receipt_ref=provider_receipt.provider_receipt_ref,
            deterministic_random_id=deterministic_random_id,
            idempotent_replay=False,
            external_effect_performed=provider_receipt.external_effect_performed,
        )
        self._store.remember(
            idempotency_key,
            _ReplayRecord(fingerprint=fingerprint, result=result),
        )
        return result


class FakePersonalTelegramTransport:
    """Synthetic adapter with no Telegram account, network or external effect."""

    def __init__(
        self,
        *,
        outcome: ProviderOutcome = ProviderOutcome.SENT,
        raise_unknown: bool = False,
    ) -> None:
        self._outcome = outcome
        self._raise_unknown = raise_unknown
        self.attempts: list[tuple[str, str, str, int]] = []

    async def send_exact(
        self,
        *,
        session_ref: str,
        peer_ref: str,
        exact_text: str,
        deterministic_random_id: int,
    ) -> ProviderSendReceipt:
        self.attempts.append(
            (session_ref, peer_ref, exact_text, deterministic_random_id)
        )
        if self._raise_unknown:
            raise TimeoutError("synthetic_unknown_outcome")
        return ProviderSendReceipt(
            outcome=self._outcome,
            provider_receipt_ref=f"synthetic_{deterministic_random_id}",
            external_effect_performed=False,
        )


def _exact_text_digest(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_EXACT_TEXT_LENGTH
        or value != value.strip()
    ):
        raise PersonalTelegramTransportError("personal_telegram_exact_text_invalid")
    return hashlib.sha256(value.encode()).hexdigest()


def _secret_digest(value: str) -> str:
    if not isinstance(value, str) or len(value) < 24 or value != value.strip():
        raise PersonalTelegramTransportError("personal_telegram_approval_token_invalid")
    return hashlib.sha256(value.encode()).hexdigest()


def _require_opaque_ref(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not 3 <= len(value) <= 128
        or value != value.strip()
        or any(character not in OPAQUE_REF_CHARACTERS for character in value)
    ):
        raise PersonalTelegramTransportError(f"personal_telegram_{field_name}_invalid")
    return value


def _deterministic_random_id(
    *,
    idempotency_key: str,
    peer_ref: str,
    exact_text_digest: str,
) -> int:
    digest = _digest(
        {
            "idempotency_key": idempotency_key,
            "peer_ref": peer_ref,
            "exact_text_digest": exact_text_digest,
        }
    )
    return int(digest[:15], 16)


def _digest(value: object) -> str:
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(wire.encode()).hexdigest()
