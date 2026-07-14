"""Read-only identity resolution for canonical Telegram ingress shadow parity."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.application.work_ownership import DirectionEvidence

TELEGRAM_SAFE_PROVIDER_ID_MAX = (1 << 52) - 1


class TelegramIngressSenderKind(StrEnum):
    """Trusted sender shapes derived from Telegram message structure."""

    HUMAN = "human"
    BOT_OR_SERVICE = "bot_or_service"
    UNKNOWN = "unknown"


class TelegramIngressDirection(StrEnum):
    """Direction result emitted by the identity snapshot boundary."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOT_OR_SERVICE = "bot_or_service"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class TelegramReplyTaskIntent(StrEnum):
    """Legacy-parity reply-task intent without performing a write."""

    CREATE_INBOUND_HUMAN_TEXT = "create_inbound_human_text"
    SATISFY_EXISTING = "satisfy_existing"
    NONE = "none"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class TelegramIdentityResolutionCode(StrEnum):
    """Payload-free resolution outcomes safe for metrics and logs."""

    DATABASE_SNAPSHOT = "database_snapshot"
    CONNECTION_NOT_FOUND = "connection_not_found"
    OWNER_IDENTITY_NOT_FOUND = "owner_identity_not_found"
    SENDER_UNRESOLVED = "sender_unresolved"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class TelegramIngressSenderIdentity:
    """Canonical sender id plus a fail-closed sender classification."""

    sender_id: str | None = field(repr=False)
    kind: TelegramIngressSenderKind


@dataclass(frozen=True)
class TelegramIngressIdentitySnapshot:
    """Account and owner identities loaded without workflow mutations."""

    account_id: str = field(repr=False)
    account_alias: str
    project_id: str = field(repr=False)
    owner_identity_ids: frozenset[str] = field(repr=False)


@dataclass(frozen=True)
class ResolveTelegramIngressIdentityCommand:
    """Inputs required to determine direction and reply-task intent."""

    business_connection_id: str = field(repr=False)
    sender_id: str | None = field(repr=False)
    text_present: bool
    sender_kind: TelegramIngressSenderKind


@dataclass(frozen=True)
class TelegramIngressIdentityResolution:
    """Redacted identity parity result for shadow comparison."""

    resolved: bool
    account_id: str | None = field(repr=False)
    account_alias: str | None
    project_id: str | None = field(repr=False)
    direction: TelegramIngressDirection
    reply_task_intent: TelegramReplyTaskIntent
    resolution: TelegramIdentityResolutionCode


class TelegramIngressIdentityReader(Protocol):
    """Read identity snapshots without commits, flushes or writes."""

    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        """Return the enabled connection snapshot, if known."""


class ResolveTelegramIngressIdentityService:
    """Resolve ingress direction through an injected read-only snapshot boundary."""

    def __init__(self, reader: TelegramIngressIdentityReader) -> None:
        self._reader = reader

    async def execute(
        self,
        command: ResolveTelegramIngressIdentityCommand,
    ) -> TelegramIngressIdentityResolution:
        snapshot = await self._reader.read(
            business_connection_id=command.business_connection_id,
        )
        if snapshot is None:
            return TelegramIngressIdentityResolution(
                resolved=False,
                account_id=None,
                account_alias=None,
                project_id=None,
                direction=TelegramIngressDirection.UNRESOLVED,
                reply_task_intent=TelegramReplyTaskIntent.UNRESOLVED,
                resolution=TelegramIdentityResolutionCode.CONNECTION_NOT_FOUND,
            )

        if command.sender_kind is TelegramIngressSenderKind.BOT_OR_SERVICE:
            direction = TelegramIngressDirection.BOT_OR_SERVICE
            reply_task_intent = TelegramReplyTaskIntent.NONE
            resolution = TelegramIdentityResolutionCode.DATABASE_SNAPSHOT
        elif (
            command.sender_kind is TelegramIngressSenderKind.UNKNOWN
            or command.sender_id is None
        ):
            direction = TelegramIngressDirection.UNRESOLVED
            reply_task_intent = TelegramReplyTaskIntent.UNRESOLVED
            resolution = TelegramIdentityResolutionCode.SENDER_UNRESOLVED
        elif not snapshot.owner_identity_ids:
            direction = TelegramIngressDirection.UNRESOLVED
            reply_task_intent = TelegramReplyTaskIntent.UNRESOLVED
            resolution = TelegramIdentityResolutionCode.OWNER_IDENTITY_NOT_FOUND
        elif command.sender_id in snapshot.owner_identity_ids:
            direction = TelegramIngressDirection.OUTBOUND
            reply_task_intent = TelegramReplyTaskIntent.SATISFY_EXISTING
            resolution = TelegramIdentityResolutionCode.DATABASE_SNAPSHOT
        else:
            direction = TelegramIngressDirection.INBOUND
            reply_task_intent = (
                TelegramReplyTaskIntent.CREATE_INBOUND_HUMAN_TEXT
                if command.text_present
                else TelegramReplyTaskIntent.NONE
            )
            resolution = TelegramIdentityResolutionCode.DATABASE_SNAPSHOT
        return TelegramIngressIdentityResolution(
            resolved=True,
            account_id=snapshot.account_id,
            account_alias=snapshot.account_alias,
            project_id=snapshot.project_id,
            direction=direction,
            reply_task_intent=reply_task_intent,
            resolution=resolution,
        )


def build_telegram_business_identity_command(
    update: Mapping[str, Any],
) -> ResolveTelegramIngressIdentityCommand | None:
    """Project one private legacy-compatible Business message into a DB lookup."""

    business_message = update.get("business_message")
    message = update.get("message")
    has_business_message = isinstance(business_message, dict)
    has_message = isinstance(message, dict)
    if has_business_message == has_message:
        return None

    selected = business_message if has_business_message else message
    if not isinstance(selected, dict):
        return None
    business_connection_id = selected.get("business_connection_id")
    if not isinstance(business_connection_id, str) or not business_connection_id.strip():
        return None
    chat = selected.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    sender = project_telegram_sender_identity(selected)
    if sender.sender_id is None:
        return None
    text = selected.get("text")
    return ResolveTelegramIngressIdentityCommand(
        business_connection_id=business_connection_id,
        sender_id=sender.sender_id,
        text_present=isinstance(text, str) and bool(text),
        sender_kind=sender.kind,
    )


def direction_evidence_from_telegram_message(
    update: Mapping[str, Any],
) -> DirectionEvidence:
    """Derive safe evidence for non-Business messages without a database read."""

    message = update.get("message")
    if not isinstance(message, dict) or isinstance(update.get("business_message"), dict):
        return DirectionEvidence.UNKNOWN
    sender = project_telegram_sender_identity(message)
    if sender.kind is TelegramIngressSenderKind.HUMAN:
        return DirectionEvidence.HUMAN_INBOUND
    if sender.kind is TelegramIngressSenderKind.BOT_OR_SERVICE:
        return DirectionEvidence.BOT_OR_SERVICE
    return DirectionEvidence.UNKNOWN


def project_telegram_sender_identity(
    message: Mapping[str, Any],
) -> TelegramIngressSenderIdentity:
    """Classify a Telegram sender without treating an unknown sender as human."""

    sender_chat = message.get("sender_chat")
    if isinstance(sender_chat, dict):
        return TelegramIngressSenderIdentity(
            sender_id=_provider_id(sender_chat.get("id")),
            kind=TelegramIngressSenderKind.BOT_OR_SERVICE,
        )

    sender = message.get("from")
    if not isinstance(sender, dict):
        return TelegramIngressSenderIdentity(
            sender_id=None,
            kind=TelegramIngressSenderKind.UNKNOWN,
        )
    sender_id = _provider_id(sender.get("id"))
    if sender_id is None:
        return TelegramIngressSenderIdentity(
            sender_id=None,
            kind=TelegramIngressSenderKind.UNKNOWN,
        )
    if sender.get("is_bot") is True:
        return TelegramIngressSenderIdentity(
            sender_id=sender_id,
            kind=TelegramIngressSenderKind.BOT_OR_SERVICE,
        )
    if sender.get("is_bot") is False:
        return TelegramIngressSenderIdentity(
            sender_id=sender_id,
            kind=TelegramIngressSenderKind.HUMAN,
        )
    return TelegramIngressSenderIdentity(
        sender_id=sender_id,
        kind=TelegramIngressSenderKind.UNKNOWN,
    )


def direction_evidence_from_identity(
    resolution: TelegramIngressIdentityResolution,
) -> DirectionEvidence:
    """Convert the typed identity result into the ownership evidence contract."""

    if resolution.direction is TelegramIngressDirection.INBOUND:
        return DirectionEvidence.HUMAN_INBOUND
    if resolution.direction is TelegramIngressDirection.OUTBOUND:
        return DirectionEvidence.OWNER_OUTBOUND
    if resolution.direction is TelegramIngressDirection.BOT_OR_SERVICE:
        return DirectionEvidence.BOT_OR_SERVICE
    return DirectionEvidence.UNKNOWN


def not_applicable_identity_resolution() -> TelegramIngressIdentityResolution:
    """Return a stable no-read result for control and non-Business traffic."""

    return TelegramIngressIdentityResolution(
        resolved=False,
        account_id=None,
        account_alias=None,
        project_id=None,
        direction=TelegramIngressDirection.NOT_APPLICABLE,
        reply_task_intent=TelegramReplyTaskIntent.NOT_APPLICABLE,
        resolution=TelegramIdentityResolutionCode.NOT_APPLICABLE,
    )


def _provider_id(value: object) -> str | None:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value != 0
        and abs(value) <= TELEGRAM_SAFE_PROVIDER_ID_MAX
    ):
        return str(value)
    return None
