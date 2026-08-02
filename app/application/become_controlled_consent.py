"""Content-free contract for explicit BECOME controlled consent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
    BecomeOwnerPresenceReader,
    opaque_subject_ref,
)

JsonObject = dict[str, Any]

APPROVED_BECOME_CONSENT_COPY = (
    "Продолжая, вы соглашаетесь на обработку отправленных данных с помощью ИИ "
    "в BECOME. Согласие можно отозвать."
)
BECOME_CONSENT_POLICY_VERSION = "become-consent-ru-v1"
CONTROLLED_CONSENT_INTAKE_SCHEMA = "gate.become-controlled-consent-intake@1"
CONSENT_INTENT = "consent_accepted"
QUEST_REF = "quest_become_investor_demo_v1"
_CONSENT_KEYS = frozenset(
    {
        "schema",
        "connector_key",
        "installation_ref",
        "group_state",
        "member_count",
        "quest_ref",
        "correlation_ref",
        "subject_ref",
        "action_ref",
        "accepted_at",
        "intent",
        "policy_version",
        "policy_digest",
        "external_effects",
        "provider_calls",
        "messages_or_publications",
        "media_bytes",
        "real_payments",
    }
)


@dataclass(frozen=True, slots=True)
class BecomeConsentEvidence:
    """Provider-local consent evidence; never part of the exported receipt."""

    provider_dialog_id: str = field(repr=False)
    provider_actor_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BecomeConsentContext:
    """Registered subject and stable quest correlation resolved from Gate."""

    installation_ref: str
    subject_ref: str
    quest_ref: str
    correlation_ref: str


@dataclass(frozen=True, slots=True)
class BecomeControlledConsentReceipt:
    schema: str
    connector_key: str
    installation_ref: str
    group_state: str
    member_count: int
    quest_ref: str
    correlation_ref: str
    subject_ref: str
    action_ref: str
    accepted_at: str
    intent: str
    policy_version: str
    policy_digest: str
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
            "quest_ref": self.quest_ref,
            "correlation_ref": self.correlation_ref,
            "subject_ref": self.subject_ref,
            "action_ref": self.action_ref,
            "accepted_at": self.accepted_at,
            "intent": self.intent,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "external_effects": self.external_effects,
            "provider_calls": self.provider_calls,
            "messages_or_publications": self.messages_or_publications,
            "media_bytes": self.media_bytes,
            "real_payments": self.real_payments,
        }
        if set(value) != _CONSENT_KEYS:
            raise BecomeControlledIntakeError("become_consent_receipt_shape_invalid")
        return value

    @property
    def digest(self) -> str:
        return _digest_object(self.payload())

    @property
    def receipt_key(self) -> str:
        return f"become_consent_{self.digest[:24]}"


@dataclass(frozen=True, slots=True)
class PersistBecomeControlledConsentCommand:
    workspace_key: str
    source_receipt_id: str
    provider_dialog_id: str = field(repr=False)
    receipt: BecomeControlledConsentReceipt


@dataclass(frozen=True, slots=True)
class PersistedBecomeControlledConsent:
    receipt_key: str
    receipt_digest: str
    duplicate: bool


class BecomeControlledConsentStore(Protocol):
    async def resolve_context(
        self,
        *,
        workspace_key: str,
        provider_dialog_id: str,
        subject_ref: str,
    ) -> BecomeConsentContext | None: ...

    async def persist(
        self,
        command: PersistBecomeControlledConsentCommand,
    ) -> PersistedBecomeControlledConsent: ...


@dataclass(frozen=True, slots=True)
class ProcessBecomeConsentCommand:
    workspace_key: str
    source_receipt_id: str
    update: JsonObject = field(repr=False)
    received_at: datetime


class ProcessBecomeConsentService:
    """Accept explicit consent only after exact registration and owner-only proof."""

    def __init__(
        self,
        *,
        store: BecomeControlledConsentStore,
        presence: BecomeOwnerPresenceReader,
        subject_pepper: str,
    ) -> None:
        if not subject_pepper:
            raise BecomeControlledIntakeError(
                "become_consent_subject_scope_incomplete"
            )
        self._store = store
        self._presence = presence
        self._subject_pepper = subject_pepper

    async def execute(
        self,
        command: ProcessBecomeConsentCommand,
    ) -> PersistedBecomeControlledConsent:
        evidence = consent_evidence(
            command.update,
            invocation_command="become",
            bot_username="BecomeNowBot",
        )
        subject_ref = opaque_subject_ref(
            provider_actor_id=evidence.provider_actor_id,
            pepper=self._subject_pepper,
        )
        context = await self._store.resolve_context(
            workspace_key=command.workspace_key,
            provider_dialog_id=evidence.provider_dialog_id,
            subject_ref=subject_ref,
        )
        if context is None:
            raise BecomeControlledIntakeError(
                "become_consent_registration_required"
            )
        member_count = await self._presence.member_count(
            installation_ref=context.installation_ref,
            provider_dialog_id=evidence.provider_dialog_id,
        )
        receipt = build_consent_receipt(
            context=context,
            source_receipt_id=command.source_receipt_id,
            accepted_at=command.received_at,
            member_count=member_count,
        )
        return await self._store.persist(
            PersistBecomeControlledConsentCommand(
                workspace_key=command.workspace_key,
                source_receipt_id=command.source_receipt_id,
                provider_dialog_id=evidence.provider_dialog_id,
                receipt=receipt,
            )
        )


def consent_evidence(
    update: JsonObject,
    *,
    invocation_command: str,
    bot_username: str,
) -> BecomeConsentEvidence:
    """Accept only the exact explicit human consent command in one group."""

    if set(update) != {"update_id", "message"} or type(update.get("update_id")) is not int:
        raise BecomeControlledIntakeError("become_consent_update_shape_invalid")
    message = update.get("message")
    if not isinstance(message, dict):
        raise BecomeControlledIntakeError("become_consent_message_missing")
    text = message.get("text")
    parts = text.strip().split() if isinstance(text, str) else []
    command = invocation_command.strip().lstrip("/").casefold()
    username = bot_username.strip().lstrip("@").casefold()
    if (
        len(parts) != 2
        or parts[1].casefold() != "consent"
        or parts[0].casefold() not in {f"/{command}", f"/{command}@{username}"}
    ):
        raise BecomeControlledIntakeError("become_consent_command_invalid")
    chat = message.get("chat")
    actor = message.get("from")
    if not isinstance(chat, dict) or chat.get("type") not in {"group", "supergroup"}:
        raise BecomeControlledIntakeError("become_consent_group_required")
    if not isinstance(actor, dict) or actor.get("is_bot") is not False:
        raise BecomeControlledIntakeError("become_consent_human_required")
    chat_id = chat.get("id")
    actor_id = actor.get("id")
    if (
        not isinstance(chat_id, (str, int))
        or isinstance(chat_id, bool)
        or not isinstance(actor_id, (str, int))
        or isinstance(actor_id, bool)
    ):
        raise BecomeControlledIntakeError("become_consent_identity_invalid")
    return BecomeConsentEvidence(str(chat_id), str(actor_id))


def is_consent_command(
    update: JsonObject,
    *,
    invocation_command: str,
    bot_username: str,
) -> bool:
    try:
        consent_evidence(
            update,
            invocation_command=invocation_command,
            bot_username=bot_username,
        )
    except BecomeControlledIntakeError:
        return False
    return True


def build_consent_receipt(
    *,
    context: BecomeConsentContext,
    source_receipt_id: str,
    accepted_at: datetime,
    member_count: int | None,
) -> BecomeControlledConsentReceipt:
    if not context.installation_ref.startswith("installation-"):
        raise BecomeControlledIntakeError("become_consent_installation_invalid")
    if not context.subject_ref.startswith("person_gate_"):
        raise BecomeControlledIntakeError("become_consent_subject_invalid")
    if context.quest_ref != QUEST_REF:
        raise BecomeControlledIntakeError("become_consent_quest_invalid")
    if not context.correlation_ref.startswith("correlation_"):
        raise BecomeControlledIntakeError("become_consent_correlation_invalid")
    if accepted_at.tzinfo is None:
        raise BecomeControlledIntakeError("become_consent_timestamp_invalid")
    if member_count != 2:
        raise BecomeControlledIntakeError("become_consent_owner_only_required")
    return BecomeControlledConsentReceipt(
        schema=CONTROLLED_CONSENT_INTAKE_SCHEMA,
        connector_key=BECOME_CONNECTOR_KEY,
        installation_ref=context.installation_ref,
        group_state="owner_only",
        member_count=2,
        quest_ref=context.quest_ref,
        correlation_ref=context.correlation_ref,
        subject_ref=context.subject_ref,
        action_ref=_derived_ref("action", source_receipt_id),
        accepted_at=accepted_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        intent=CONSENT_INTENT,
        policy_version=BECOME_CONSENT_POLICY_VERSION,
        policy_digest=hashlib.sha256(
            APPROVED_BECOME_CONSENT_COPY.encode("utf-8")
        ).hexdigest(),
    )


def consent_correlation_ref(*, registration_receipt_digest: str) -> str:
    if len(registration_receipt_digest) != 64:
        raise BecomeControlledIntakeError(
            "become_consent_registration_digest_invalid"
        )
    return _derived_ref("correlation", registration_receipt_digest)


def _derived_ref(prefix: str, value: str) -> str:
    if not value:
        raise BecomeControlledIntakeError("become_consent_reference_input_invalid")
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _digest_object(value: JsonObject) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()



RELATIONS_CONSENT_COMMAND_SCHEMA = "relations.controlled-consent-command@1"
RELATIONS_CONSENT_PURPOSE_REF = "BECOME_NOW_QUEST"


def parse_controlled_consent_receipt(
    value: object,
) -> BecomeControlledConsentReceipt:
    """Rehydrate only an exact previously validated consent receipt."""

    if not isinstance(value, dict) or set(value) != _CONSENT_KEYS:
        raise BecomeControlledIntakeError("become_consent_receipt_shape_invalid")
    expected_strings = (
        "schema",
        "connector_key",
        "installation_ref",
        "group_state",
        "quest_ref",
        "correlation_ref",
        "subject_ref",
        "action_ref",
        "accepted_at",
        "intent",
        "policy_version",
        "policy_digest",
    )
    if any(not isinstance(value.get(key), str) for key in expected_strings):
        raise BecomeControlledIntakeError("become_consent_receipt_type_invalid")
    if (
        value["schema"] != CONTROLLED_CONSENT_INTAKE_SCHEMA
        or value["connector_key"] != BECOME_CONNECTOR_KEY
        or value["group_state"] != "owner_only"
        or value["member_count"] != 2
        or value["intent"] != CONSENT_INTENT
        or value["policy_version"] != BECOME_CONSENT_POLICY_VERSION
        or value["policy_digest"]
        != hashlib.sha256(APPROVED_BECOME_CONSENT_COPY.encode("utf-8")).hexdigest()
        or any(
            value[key] != 0
            for key in (
                "external_effects",
                "provider_calls",
                "messages_or_publications",
                "media_bytes",
                "real_payments",
            )
        )
    ):
        raise BecomeControlledIntakeError("become_consent_receipt_invalid")
    return BecomeControlledConsentReceipt(
        schema=str(value["schema"]),
        connector_key=str(value["connector_key"]),
        installation_ref=str(value["installation_ref"]),
        group_state=str(value["group_state"]),
        member_count=2,
        quest_ref=str(value["quest_ref"]),
        correlation_ref=str(value["correlation_ref"]),
        subject_ref=str(value["subject_ref"]),
        action_ref=str(value["action_ref"]),
        accepted_at=str(value["accepted_at"]),
        intent=str(value["intent"]),
        policy_version=str(value["policy_version"]),
        policy_digest=str(value["policy_digest"]),
    )


def project_relations_consent_command(
    receipt: BecomeControlledConsentReceipt,
) -> JsonObject:
    """Project the exact content-free command accepted by Relations."""

    payload = receipt.payload()
    command: JsonObject = {
        "schema": RELATIONS_CONSENT_COMMAND_SCHEMA,
        "command": "grant_consent",
        "mode": "CONTROLLED",
        "quest_ref": payload["quest_ref"],
        "correlation_ref": payload["correlation_ref"],
        "person_ref": payload["subject_ref"],
        "purpose_ref": RELATIONS_CONSENT_PURPOSE_REF,
        "policy_version": payload["policy_version"],
        "policy_digest": payload["policy_digest"],
        "evidence_ref": _derived_object_ref(
            "consent_evidence",
            {
                "installation_ref": payload["installation_ref"],
                "action_ref": payload["action_ref"],
            },
        ),
        "effective_at": payload["accepted_at"],
        "valid_until": None,
        "prior_receipt_ref": None,
        "idempotency_key": _derived_object_ref(
            "idem_consent",
            {
                "quest_ref": payload["quest_ref"],
                "action_ref": payload["action_ref"],
                "policy_digest": payload["policy_digest"],
            },
        ),
        "next_event": "consent.granted",
        "execution_state": "projected_not_executed",
        "external_effects": 0,
        "provider_calls": 0,
        "messages_or_publications": 0,
        "media_bytes": 0,
        "real_payments": 0,
        "contains_pii": False,
    }
    command["command_digest"] = _digest_object(command)
    return command


def _derived_object_ref(prefix: str, value: JsonObject) -> str:
    return f"{prefix}_{_digest_object(value)[:24]}"
