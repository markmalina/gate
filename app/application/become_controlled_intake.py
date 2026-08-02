"""Content-free contracts for the controlled BECOME Telegram intake."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

JsonObject = dict[str, Any]

CONTROLLED_INTAKE_SCHEMA = "gate.become-controlled-intake@1"
BECOME_CONNECTOR_KEY = "telegram_become_group"
REGISTRATION_INTENT = "registration_confirmed"
_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "connector_key",
        "installation_ref",
        "group_state",
        "member_count",
        "subject_ref",
        "update_ref",
        "received_at",
        "intent",
        "external_effects",
        "provider_calls",
        "messages_or_publications",
        "media_bytes",
        "real_payments",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "text",
        "message_text",
        "chat_id",
        "telegram_id",
        "username",
        "first_name",
        "last_name",
        "phone",
        "email",
        "token",
        "secret",
        "provider_payload",
        "transcript",
        "media",
    }
)


class BecomeControlledIntakeError(ValueError):
    """Fail-closed controlled-intake contract error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class BecomeRegistrationEvidence:
    """Provider-local registration evidence; never part of the exported receipt."""

    provider_dialog_id: str = field(repr=False)
    provider_actor_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BecomeControlledIntakeReceipt:
    schema: str
    connector_key: str
    installation_ref: str
    group_state: str
    member_count: int
    subject_ref: str
    update_ref: str
    received_at: str
    intent: str
    external_effects: int = 0
    provider_calls: int = 0
    messages_or_publications: int = 0
    media_bytes: int = 0
    real_payments: int = 0

    def payload(self) -> JsonObject:
        value: JsonObject = {
            "schema": self.schema,
            "connector_key": self.connector_key,
            "installation_ref": self.installation_ref,
            "group_state": self.group_state,
            "member_count": self.member_count,
            "subject_ref": self.subject_ref,
            "update_ref": self.update_ref,
            "received_at": self.received_at,
            "intent": self.intent,
            "external_effects": self.external_effects,
            "provider_calls": self.provider_calls,
            "messages_or_publications": self.messages_or_publications,
            "media_bytes": self.media_bytes,
            "real_payments": self.real_payments,
        }
        if set(value) != _RECEIPT_KEYS:
            raise BecomeControlledIntakeError("become_intake_receipt_shape_invalid")
        _reject_sensitive_keys(value)
        return value

    @property
    def digest(self) -> str:
        return _digest(self.payload())

    @property
    def receipt_key(self) -> str:
        return f"become_intake_{self.digest[:24]}"


@dataclass(frozen=True, slots=True)
class PersistBecomeControlledIntakeCommand:
    workspace_key: str
    source_receipt_id: str
    provider_dialog_id: str = field(repr=False)
    receipt: BecomeControlledIntakeReceipt


@dataclass(frozen=True, slots=True)
class PersistedBecomeControlledIntake:
    receipt_key: str
    receipt_digest: str
    duplicate: bool


class BecomeControlledIntakeStore(Protocol):
    async def persist(
        self,
        command: PersistBecomeControlledIntakeCommand,
    ) -> PersistedBecomeControlledIntake: ...


class BecomeOwnerPresenceReader(Protocol):
    async def member_count(
        self,
        *,
        installation_ref: str,
        provider_dialog_id: str,
    ) -> int | None: ...


@dataclass(frozen=True, slots=True)
class ProcessBecomeRegistrationCommand:
    workspace_key: str
    source_receipt_id: str
    installation_ref: str
    update: JsonObject = field(repr=False)
    received_at: datetime


class ProcessBecomeRegistrationService:
    """Materialize one exact registration after a current owner-only observation."""

    def __init__(
        self,
        *,
        store: BecomeControlledIntakeStore,
        presence: BecomeOwnerPresenceReader,
        subject_pepper: str,
    ) -> None:
        if not subject_pepper:
            raise BecomeControlledIntakeError(
                "become_registration_subject_scope_incomplete"
            )
        self._store = store
        self._presence = presence
        self._subject_pepper = subject_pepper

    async def execute(
        self,
        command: ProcessBecomeRegistrationCommand,
    ) -> PersistedBecomeControlledIntake:
        evidence = registration_evidence(
            command.update,
            invocation_command="become",
            bot_username="BecomeNowBot",
        )
        member_count = await self._presence.member_count(
            installation_ref=command.installation_ref,
            provider_dialog_id=evidence.provider_dialog_id,
        )
        receipt = build_registration_receipt(
            installation_ref=command.installation_ref,
            subject_ref=opaque_subject_ref(
                provider_actor_id=evidence.provider_actor_id,
                pepper=self._subject_pepper,
            ),
            source_receipt_id=command.source_receipt_id,
            received_at=command.received_at,
            member_count=member_count,
        )
        return await self._store.persist(
            PersistBecomeControlledIntakeCommand(
                workspace_key=command.workspace_key,
                source_receipt_id=command.source_receipt_id,
                provider_dialog_id=evidence.provider_dialog_id,
                receipt=receipt,
            )
        )


def registration_evidence(
    update: JsonObject,
    *,
    invocation_command: str,
    bot_username: str,
) -> BecomeRegistrationEvidence:
    """Accept only the exact human registration command in one group."""

    if set(update) != {"update_id", "message"} or type(update.get("update_id")) is not int:
        raise BecomeControlledIntakeError("become_registration_update_shape_invalid")
    message = update.get("message")
    if not isinstance(message, dict):
        raise BecomeControlledIntakeError("become_registration_message_missing")
    text = message.get("text")
    parts = text.strip().split() if isinstance(text, str) else []
    command = invocation_command.strip().lstrip("/").casefold()
    username = bot_username.strip().lstrip("@").casefold()
    if (
        len(parts) != 2
        or parts[1].casefold() != "register"
        or parts[0].casefold() not in {f"/{command}", f"/{command}@{username}"}
    ):
        raise BecomeControlledIntakeError("become_registration_command_invalid")
    chat = message.get("chat")
    actor = message.get("from")
    if not isinstance(chat, dict) or chat.get("type") not in {"group", "supergroup"}:
        raise BecomeControlledIntakeError("become_registration_group_required")
    if not isinstance(actor, dict) or actor.get("is_bot") is not False:
        raise BecomeControlledIntakeError("become_registration_human_required")
    chat_id = chat.get("id")
    actor_id = actor.get("id")
    if (
        not isinstance(chat_id, (str, int))
        or isinstance(chat_id, bool)
        or not isinstance(actor_id, (str, int))
        or isinstance(actor_id, bool)
    ):
        raise BecomeControlledIntakeError("become_registration_identity_invalid")
    return BecomeRegistrationEvidence(str(chat_id), str(actor_id))


def opaque_subject_ref(*, provider_actor_id: str, pepper: str) -> str:
    """Pseudonymize a provider identity without exporting or storing it."""

    if not provider_actor_id or not pepper:
        raise BecomeControlledIntakeError("become_registration_subject_scope_incomplete")
    digest = hmac.new(
        pepper.encode("utf-8"),
        f"{BECOME_CONNECTOR_KEY}:{provider_actor_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"person_gate_{digest[:24]}"


def opaque_update_ref(*, source_receipt_id: str) -> str:
    if not source_receipt_id:
        raise BecomeControlledIntakeError("become_registration_source_receipt_required")
    digest = hashlib.sha256(source_receipt_id.encode("utf-8")).hexdigest()
    return f"telegram_update_{digest[:24]}"


def build_registration_receipt(
    *,
    installation_ref: str,
    subject_ref: str,
    source_receipt_id: str,
    received_at: datetime,
    member_count: int | None,
) -> BecomeControlledIntakeReceipt:
    if not installation_ref.startswith("installation-"):
        raise BecomeControlledIntakeError("become_registration_installation_invalid")
    if not subject_ref.startswith("person_gate_"):
        raise BecomeControlledIntakeError("become_registration_subject_invalid")
    if received_at.tzinfo is None:
        raise BecomeControlledIntakeError("become_registration_timestamp_invalid")
    if member_count != 2:
        raise BecomeControlledIntakeError("become_registration_owner_only_required")
    return BecomeControlledIntakeReceipt(
        schema=CONTROLLED_INTAKE_SCHEMA,
        connector_key=BECOME_CONNECTOR_KEY,
        installation_ref=installation_ref,
        group_state="owner_only",
        member_count=member_count,
        subject_ref=subject_ref,
        update_ref=opaque_update_ref(source_receipt_id=source_receipt_id),
        received_at=received_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        intent=REGISTRATION_INTENT,
    )


def _digest(value: JsonObject) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _SENSITIVE_KEYS:
                raise BecomeControlledIntakeError("become_intake_sensitive_key_forbidden")
            _reject_sensitive_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_keys(child)
