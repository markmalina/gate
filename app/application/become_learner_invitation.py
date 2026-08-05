"""Default-off Slice 3 learner invitation domain boundary.

The module is deliberately provider-neutral.  Gate's PostgreSQL adapter owns
atomicity and durable idempotency; this layer owns the exact identity, PROGRAM,
consent, callback and effect-scope contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

PROGRAM_ID = "60a45dfd-ffe8-4576-acff-cf75cf0c3c9c"
PROGRAM_VERSION = 2
PROGRAM_DIGEST = "710c580c210fa687af57962bc1f5011dbc88341f20ee1b9d3ae83ad352d0581d"
CONSENT_POLICY_VERSION = "become.slice3.learner-link-consent.ru@1"
CONSENT_POLICY_DIGEST = "2a68687260c6e1d16fb2ba0191500ebb36c1a4018e692901760ad1cedd102d4e"
RELATIONS_RECEIPT_SCHEMA = "relations.become-slice3-learner-link-receipt@1"
RELATIONS_EVENT = "expert.invited_to_exact_program.learner"
CONSENT_TEXT = (
    "Я принимаю это приглашение Become и разрешаю связать мой Telegram-аккаунт и этот "
    "приватный чат с указанными приглашением, экспертом и PROGRAM digest; сохранить факт "
    "принятия и передать Relations только непрозрачные идентификаторы и digests для "
    "фиксации этой связи."
)
CALLBACK_PREFIX = "b3"
ACCEPT_ACTION = "a"
DECLINE_ACTION = "d"
JsonObject = dict[str, Any]


class Slice3Error(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Slice3Disposition(StrEnum):
    DISABLED = "disabled"
    NOT_APPLICABLE = "not_applicable"
    CANDIDATE_REGISTERED = "candidate_registered"
    CANDIDATE_REPLAYED = "candidate_replayed"
    INVITATION_CREATED = "invitation_created"
    INVITATION_REPLAYED = "invitation_replayed"
    ACCEPTED = "accepted"
    ACCEPTED_DUPLICATE = "accepted_duplicate"
    DECLINED = "declined"
    DECLINED_DUPLICATE = "declined_duplicate"


@dataclass(frozen=True, slots=True)
class CandidateBinding:
    candidate_id: str
    subject_ref: str
    learner_binding_digest: str
    provider_dialog_id: str = field(repr=False)
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class PinInvitationCommand:
    workspace_key: str
    source_receipt_id: str
    expert_subject_ref: str
    expert_dialog_id: str = field(repr=False)
    learner_binding_digest: str
    become_space_ref: str
    bot_installation_identity: str
    program_id: str = PROGRAM_ID
    approved_version: int = PROGRAM_VERSION
    approved_digest: str = PROGRAM_DIGEST
    consent_policy_version: str = CONSENT_POLICY_VERSION
    consent_policy_digest: str = CONSENT_POLICY_DIGEST


@dataclass(frozen=True, slots=True)
class InvitationReceipt:
    invitation_id: str
    invitation_digest: str
    learner_binding_digest: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class AcceptanceReceipt:
    acceptance_id: str | None
    invitation_id: str
    disposition: Slice3Disposition
    relations_command_digest: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    delivery_id: str
    kind: str
    provider_dialog_id: str = field(repr=False)
    invitation_id: str
    invitation_digest: str
    callback_ref: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RelationsJob:
    outbox_id: str
    idempotency_key: str
    command: JsonObject
    command_digest: str


class Slice3Store(Protocol):
    async def register_candidate(
        self,
        *,
        workspace_key: str,
        source_receipt_id: str,
        subject_ref: str,
        telegram_actor_id: str,
        private_dialog_id: str,
        bot_installation_identity: str,
        become_space_ref: str,
        learner_binding_digest: str,
        now: datetime,
    ) -> CandidateBinding: ...

    async def pin_and_create_invitation(
        self, *, command: PinInvitationCommand, now: datetime
    ) -> InvitationReceipt: ...

    async def apply_learner_callback(
        self,
        *,
        action: str,
        callback_ref: str,
        subject_ref: str,
        private_dialog_id: str,
        source_receipt_id: str,
        consent_policy_version: str,
        consent_policy_digest: str,
        now: datetime,
    ) -> AcceptanceReceipt: ...

    async def claim_delivery(self, *, now: datetime, max_sends: int) -> DeliveryJob | None: ...

    async def settle_delivery(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        now: datetime,
    ) -> None: ...

    async def claim_relations_post(
        self, *, now: datetime, max_posts: int
    ) -> RelationsJob | None: ...

    async def settle_relations_post(
        self,
        *,
        outbox_id: str,
        outcome: str,
        receipt_digest: str | None,
        now: datetime,
    ) -> None: ...

    async def status(self, *, now: datetime) -> JsonObject: ...

    async def rollback_preflight(self) -> JsonObject: ...

    async def purge_expired(self, *, now: datetime) -> JsonObject: ...


class Slice3Telegram(Protocol):
    async def send_message(
        self, *, chat_id: str, text: str, reply_markup: JsonObject | None = None
    ) -> JsonObject: ...

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> JsonObject: ...


class Slice3Relations(Protocol):
    async def post(self, *, command: JsonObject, idempotency_key: str) -> JsonObject: ...


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def learner_binding_digest(
    *,
    telegram_actor_id: str,
    private_dialog_id: str,
    bot_installation_identity: str,
    become_space_ref: str,
) -> str:
    values = {
        "become_space_ref": _nonempty(become_space_ref, "become_space_ref_invalid"),
        "bot_installation_identity": _nonempty(
            bot_installation_identity, "bot_installation_identity_invalid"
        ),
        "private_dialog_id": _telegram_id(private_dialog_id, "private_dialog_id_invalid"),
        "telegram_actor_id": _telegram_id(telegram_actor_id, "telegram_actor_id_invalid"),
    }
    return canonical_digest(values)


def validate_pin_command(command: PinInvitationCommand) -> None:
    if (
        command.program_id != PROGRAM_ID
        or command.approved_version != PROGRAM_VERSION
        or command.approved_digest != PROGRAM_DIGEST
    ):
        raise Slice3Error("slice3_program_binding_mismatch")
    if (
        command.consent_policy_version != CONSENT_POLICY_VERSION
        or command.consent_policy_digest != CONSENT_POLICY_DIGEST
        or hashlib.sha256(CONSENT_TEXT.encode()).hexdigest() != CONSENT_POLICY_DIGEST
    ):
        raise Slice3Error("slice3_consent_policy_mismatch")
    if not _digest(command.learner_binding_digest):
        raise Slice3Error("slice3_learner_binding_digest_invalid")
    for value, code in (
        (command.workspace_key, "slice3_workspace_invalid"),
        (command.source_receipt_id, "slice3_source_receipt_invalid"),
        (command.expert_subject_ref, "slice3_expert_subject_invalid"),
        (command.expert_dialog_id, "slice3_expert_dialog_invalid"),
        (command.become_space_ref, "slice3_space_invalid"),
        (command.bot_installation_identity, "slice3_installation_invalid"),
    ):
        _nonempty(value, code)


class ProcessSlice3Update:
    """Parse only exact private preflight, owner pin and learner callbacks."""

    def __init__(
        self,
        *,
        store: Slice3Store,
        identity_pepper: str,
        workspace_key: str,
        become_space_ref: str,
        bot_installation_identity: str,
        enabled: bool,
        data_gate_approved: bool,
    ) -> None:
        self._store = store
        self._pepper = _nonempty(identity_pepper, "slice3_identity_pepper_missing")
        self._workspace = workspace_key
        self._space = become_space_ref
        self._installation = bot_installation_identity
        self._enabled = enabled
        self._data_gate = data_gate_approved

    async def execute(
        self, *, update: JsonObject, source_receipt_id: str, now: datetime
    ) -> Slice3Disposition:
        event = parse_slice3_update(update)
        if event is None:
            return Slice3Disposition.NOT_APPLICABLE
        if not self._enabled or not self._data_gate:
            raise Slice3Error("slice3_disabled")
        kind, actor_id, dialog_id, value, callback_query_id = event
        subject_ref = _subject_ref(actor_id, self._pepper)
        if kind == "preflight":
            digest = learner_binding_digest(
                telegram_actor_id=actor_id,
                private_dialog_id=dialog_id,
                bot_installation_identity=self._installation,
                become_space_ref=self._space,
            )
            binding = await self._store.register_candidate(
                workspace_key=self._workspace,
                source_receipt_id=source_receipt_id,
                subject_ref=subject_ref,
                telegram_actor_id=actor_id,
                private_dialog_id=dialog_id,
                bot_installation_identity=self._installation,
                become_space_ref=self._space,
                learner_binding_digest=digest,
                now=now,
            )
            return (
                Slice3Disposition.CANDIDATE_REPLAYED
                if binding.replayed
                else Slice3Disposition.CANDIDATE_REGISTERED
            )
        if kind == "invite":
            command = PinInvitationCommand(
                workspace_key=self._workspace,
                source_receipt_id=source_receipt_id,
                expert_subject_ref=subject_ref,
                expert_dialog_id=dialog_id,
                learner_binding_digest=value,
                become_space_ref=self._space,
                bot_installation_identity=self._installation,
            )
            validate_pin_command(command)
            invitation_receipt = await self._store.pin_and_create_invitation(
                command=command, now=now
            )
            return (
                Slice3Disposition.INVITATION_REPLAYED
                if invitation_receipt.replayed
                else Slice3Disposition.INVITATION_CREATED
            )
        action, callback_ref = value.split(":", 1)
        acceptance_receipt = await self._store.apply_learner_callback(
            action=action,
            callback_ref=callback_ref,
            subject_ref=subject_ref,
            private_dialog_id=dialog_id,
            source_receipt_id=source_receipt_id,
            consent_policy_version=CONSENT_POLICY_VERSION,
            consent_policy_digest=CONSENT_POLICY_DIGEST,
            now=now,
        )
        return acceptance_receipt.disposition


class DeliverSlice3:
    def __init__(
        self,
        *,
        store: Slice3Store,
        telegram: Slice3Telegram,
        enabled: bool,
        max_sends: int,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._enabled = enabled
        self._max_sends = max_sends

    async def run_once(self, *, now: datetime) -> str:
        if not self._enabled or self._max_sends < 1:
            return "disabled"
        job = await self._store.claim_delivery(now=now, max_sends=self._max_sends)
        if job is None:
            return "idle"
        text, markup = delivery_body(job)
        try:
            response = await self._telegram.send_message(
                chat_id=job.provider_dialog_id, text=text, reply_markup=markup
            )
            message_id = response.get("result", {}).get("message_id")
            if type(message_id) is not int:
                raise Slice3Error("slice3_telegram_result_invalid")
        except Exception:
            await self._store.settle_delivery(
                delivery_id=job.delivery_id,
                outcome="unknown",
                telegram_message_id=None,
                now=now,
            )
            return "unknown"
        await self._store.settle_delivery(
            delivery_id=job.delivery_id,
            outcome="sent",
            telegram_message_id=str(message_id),
            now=now,
        )
        return "sent"


class PostSlice3Relations:
    def __init__(
        self,
        *,
        store: Slice3Store,
        relations: Slice3Relations,
        enabled: bool,
        max_posts: int,
    ) -> None:
        self._store = store
        self._relations = relations
        self._enabled = enabled
        self._max_posts = max_posts

    async def run_once(self, *, now: datetime) -> str:
        if not self._enabled or self._max_posts != 1:
            return "disabled"
        job = await self._store.claim_relations_post(now=now, max_posts=self._max_posts)
        if job is None:
            return "idle"
        if canonical_digest(job.command) != job.command_digest:
            raise Slice3Error("slice3_relations_command_digest_mismatch")
        try:
            response = await self._relations.post(
                command=job.command, idempotency_key=job.idempotency_key
            )
            digest = validate_relations_receipt(response=response, job=job)
        except Exception:
            await self._store.settle_relations_post(
                outbox_id=job.outbox_id, outcome="unknown", receipt_digest=None, now=now
            )
            return "unknown"
        await self._store.settle_relations_post(
            outbox_id=job.outbox_id, outcome="executed", receipt_digest=digest, now=now
        )
        return "executed"


def validate_relations_receipt(*, response: object, job: RelationsJob) -> str:
    """Accept only the exact content-free receipt bound to the reserved command."""

    if not isinstance(response, dict):
        raise Slice3Error("slice3_relations_receipt_invalid")
    expected_keys = {
        "schema",
        "service",
        "source_sha",
        "idempotency_key",
        "command_digest",
        "event",
        "invitation_ref",
        "invitation_digest",
        "program_ref",
        "program_version",
        "program_digest",
        "expert_person_ref",
        "learner_person_ref",
        "learner_binding_digest",
        "become_space_ref",
        "consent_policy_version",
        "consent_policy_digest",
        "acceptance_digest",
        "status",
        "domain_events",
        "external_effects",
        "provider_calls",
        "messages_or_publications",
        "exercises",
        "progress_or_results",
        "payments",
        "mayaki_oko_take_effects",
        "contains_provider_identifiers",
        "receipt_digest",
    }
    if set(response) != expected_keys:
        raise Slice3Error("slice3_relations_receipt_shape_invalid")
    command = job.command
    try:
        invitation = command["invitation"]
        program = command["program"]
        participants = command["participants"]
        expert = participants["expert"]
        learner = participants["learner"]
        consent = command["consent"]
        scope = command["scope"]
    except (KeyError, TypeError) as exc:
        raise Slice3Error("slice3_relations_command_shape_invalid") from exc
    exact = {
        "schema": RELATIONS_RECEIPT_SCHEMA,
        "service": "relations",
        "idempotency_key": job.idempotency_key,
        "command_digest": job.command_digest,
        "event": RELATIONS_EVENT,
        "invitation_ref": invitation["invitation_ref"],
        "invitation_digest": invitation["invitation_digest"],
        "program_ref": program["program_ref"],
        "program_version": program["version"],
        "program_digest": program["digest"],
        "expert_person_ref": expert["person_ref"],
        "learner_person_ref": learner["person_ref"],
        "learner_binding_digest": learner["binding_digest"],
        "become_space_ref": scope["become_space_ref"],
        "consent_policy_version": consent["policy_version"],
        "consent_policy_digest": consent["policy_digest"],
        "acceptance_digest": consent["acceptance_digest"],
        "status": "executed",
        "domain_events": 1,
        "external_effects": 0,
        "provider_calls": 0,
        "messages_or_publications": 0,
        "exercises": 0,
        "progress_or_results": 0,
        "payments": 0,
        "mayaki_oko_take_effects": 0,
        "contains_provider_identifiers": False,
    }
    for key, expected in exact.items():
        if response.get(key) != expected:
            raise Slice3Error(f"slice3_relations_receipt_binding_invalid:{key}")
    source_sha = response.get("source_sha")
    if not isinstance(source_sha, str) or re.fullmatch(r"[a-f0-9]{40}", source_sha) is None:
        raise Slice3Error("slice3_relations_receipt_source_invalid")
    receipt_digest = response.get("receipt_digest")
    unsigned = {key: value for key, value in response.items() if key != "receipt_digest"}
    if (
        not isinstance(receipt_digest, str)
        or not _digest(receipt_digest)
        or canonical_digest(unsigned) != receipt_digest
    ):
        raise Slice3Error("slice3_relations_receipt_digest_mismatch")
    return receipt_digest


def parse_slice3_update(
    update: JsonObject,
) -> tuple[str, str, str, str, str | None] | None:
    if type(update.get("update_id")) is not int:
        raise Slice3Error("slice3_update_id_invalid")
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        actor_id, dialog_id, chat_type = _telegram_actor(
            callback.get("from"), callback.get("message")
        )
        if chat_type != "private":
            raise Slice3Error("slice3_callback_private_chat_required")
        data = callback.get("data")
        callback_id = callback.get("id")
        if not isinstance(data, str) or not isinstance(callback_id, str) or not callback_id:
            raise Slice3Error("slice3_callback_invalid")
        parts = data.split(":")
        if (
            len(parts) != 3
            or parts[0] != CALLBACK_PREFIX
            or parts[1]
            not in {
                ACCEPT_ACTION,
                DECLINE_ACTION,
            }
            or not re.fullmatch(r"[a-f0-9]{32}", parts[2])
        ):
            raise Slice3Error("slice3_callback_invalid")
        return "callback", actor_id, dialog_id, f"{parts[1]}:{parts[2]}", callback_id
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str):
        return None
    actor_id, dialog_id, chat_type = _telegram_actor(message.get("from"), message)
    normalized = " ".join(text.strip().split())
    if chat_type == "private" and normalized == "/become learner":
        return "preflight", actor_id, dialog_id, "", None
    prefix = "/become invite "
    if chat_type in {"group", "supergroup"} and normalized.startswith(prefix):
        digest = normalized.removeprefix(prefix)
        if not _digest(digest):
            raise Slice3Error("slice3_learner_binding_digest_invalid")
        return "invite", actor_id, dialog_id, digest, None
    return None


def delivery_body(job: DeliveryJob) -> tuple[str, JsonObject | None]:
    if job.kind == "invitation":
        return (
            CONSENT_TEXT,
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "Принять",
                            "callback_data": (
                                f"{CALLBACK_PREFIX}:{ACCEPT_ACTION}:{job.callback_ref}"
                            ),
                        },
                        {
                            "text": "Отказаться",
                            "callback_data": (
                                f"{CALLBACK_PREFIX}:{DECLINE_ACTION}:{job.callback_ref}"
                            ),
                        },
                    ]
                ]
            },
        )
    if job.kind == "confirmation":
        return "Приглашение принято. Связь с программой зафиксирована.", None
    raise Slice3Error("slice3_delivery_kind_invalid")


def _telegram_actor(actor: object, message: object) -> tuple[str, str, str]:
    if not isinstance(actor, dict) or actor.get("is_bot") is not False:
        raise Slice3Error("slice3_actor_invalid")
    if not isinstance(message, dict) or not isinstance(message.get("chat"), dict):
        raise Slice3Error("slice3_message_invalid")
    chat = message["chat"]
    actor_id = actor.get("id")
    dialog_id = chat.get("id")
    chat_type = chat.get("type")
    if isinstance(actor_id, bool) or isinstance(dialog_id, bool):
        raise Slice3Error("slice3_identity_invalid")
    if not isinstance(actor_id, (str, int)) or not isinstance(dialog_id, (str, int)):
        raise Slice3Error("slice3_identity_invalid")
    if chat_type not in {"private", "group", "supergroup"}:
        raise Slice3Error("slice3_chat_type_invalid")
    return str(actor_id), str(dialog_id), chat_type


def _subject_ref(actor_id: str, pepper: str) -> str:
    return "subject://telegram/" + hashlib.sha256(f"{pepper}:{actor_id}".encode()).hexdigest()


def _telegram_id(value: str, code: str) -> str:
    normalized = _nonempty(value, code)
    if re.fullmatch(r"-?[1-9][0-9]{0,19}", normalized) is None:
        raise Slice3Error(code)
    return normalized


def _nonempty(value: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 255:
        raise Slice3Error(code)
    return value.strip()


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None
