"""Typed, secret-free definitions for Telegram transport connectors.

The registry is trusted application configuration. Telegram payloads cannot select
their connector, role, capabilities or send authorization. Alpha is deliberately
dormant until its own webhook, credentials and delivery path are implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TelegramConnectorKey(StrEnum):
    """Stable connector identities, independent of bot usernames."""

    BUSINESS = "telegram_business"
    ALPHA_GROUP = "telegram_alpha_group"


class TelegramConnectorRole(StrEnum):
    """Roles that a trusted connector definition may assign to ingress."""

    BUSINESS = "business"
    ALPHA_GROUP = "alpha_group"
    UNKNOWN = "unknown"


class TelegramConnectorActivation(StrEnum):
    """Whether a connector may participate in runtime processing."""

    ACTIVE = "active"
    DORMANT = "dormant"


class TelegramConnectorCapability(StrEnum):
    """Transport features supported by one connector type."""

    BOT_MESSAGES = "bot_messages"
    CALLBACK_QUERIES = "callback_queries"
    BUSINESS_CONNECTIONS = "business_connections"
    BUSINESS_MESSAGES = "business_messages"
    GROUP_MESSAGES = "group_messages"


class TelegramSendAuthorization(StrEnum):
    """Send authorities that may exist on a connector."""

    LEGACY_SYSTEM_REPLY = "legacy_system_reply"
    HUMAN_CONFIRMED = "human_confirmed"
    AGENT_DELIVERY_GRANT = "agent_delivery_grant"


BUSINESS_ALLOWED_UPDATES = (
    "message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
)
ALPHA_GROUP_ALLOWED_UPDATES = ("message", "my_chat_member")


class TelegramConnectorResolutionError(RuntimeError):
    """A bounded connector-resolution failure safe for worker persistence."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class TelegramConnectorDefinition:
    """Immutable connector policy containing secret references, never values."""

    key: TelegramConnectorKey
    role: TelegramConnectorRole
    activation: TelegramConnectorActivation
    bot_identity: str | None = field(repr=False)
    bot_identity_ref: str = field(repr=False)
    bot_token_secret_ref: str = field(repr=False)
    webhook_secret_ref: str = field(repr=False)
    webhook_path: str = field(repr=False)
    allowed_updates: tuple[str, ...] = field(repr=False)
    declared_capabilities: frozenset[TelegramConnectorCapability] = field(repr=False)
    send_authorizations: frozenset[TelegramSendAuthorization] = field(repr=False)
    worker_enabled: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        """Reject definitions that could blur Business and Alpha boundaries."""

        if self.role is TelegramConnectorRole.UNKNOWN:
            raise ValueError("a configured connector cannot have an unknown role")
        if not all(
            reference.strip()
            for reference in (
                self.bot_identity_ref,
                self.bot_token_secret_ref,
                self.webhook_secret_ref,
            )
        ):
            raise ValueError("connector identity and secret references are required")
        if not self.webhook_path.startswith("/"):
            raise ValueError("connector webhook path must be absolute")
        if not self.allowed_updates or len(set(self.allowed_updates)) != len(
            self.allowed_updates
        ):
            raise ValueError("connector allowed updates must be non-empty and unique")
        if self.activation is TelegramConnectorActivation.ACTIVE and not _bot_identity(
            self.bot_identity
        ):
            raise ValueError("an active connector requires a bot identity")
        if self.worker_enabled and self.activation is not TelegramConnectorActivation.ACTIVE:
            raise ValueError("a dormant connector cannot enable its worker")

        if self.key is TelegramConnectorKey.BUSINESS:
            if self.role is not TelegramConnectorRole.BUSINESS:
                raise ValueError("the Business connector requires the Business role")
            if TelegramConnectorCapability.GROUP_MESSAGES in self.declared_capabilities:
                raise ValueError("the Business connector cannot host the Alpha group agent")
            if TelegramSendAuthorization.AGENT_DELIVERY_GRANT in self.send_authorizations:
                raise ValueError("the Business connector has no agent delivery authority")
        elif self.key is TelegramConnectorKey.ALPHA_GROUP:
            if self.role is not TelegramConnectorRole.ALPHA_GROUP:
                raise ValueError("the Alpha connector requires the Alpha group role")
            if self.activation is not TelegramConnectorActivation.DORMANT:
                raise ValueError("the Alpha connector is not activatable in this release")
            if self.allowed_updates != ALPHA_GROUP_ALLOWED_UPDATES:
                raise ValueError("the Alpha connector update allowlist is release-pinned")
            if self.declared_capabilities != frozenset(
                {TelegramConnectorCapability.GROUP_MESSAGES}
            ):
                raise ValueError("the Alpha connector capabilities are release-pinned")
            if self.send_authorizations:
                raise ValueError("the dormant Alpha connector cannot authorize sends")

    @property
    def effective_capabilities(self) -> frozenset[TelegramConnectorCapability]:
        """Return no runtime capabilities for a dormant connector."""

        if self.activation is TelegramConnectorActivation.DORMANT:
            return frozenset()
        return self.declared_capabilities

    def matches_work_item_key(self, bot_key: str) -> bool:
        """Match a trusted envelope key without consulting its Telegram payload."""

        normalized = bot_key.strip().lstrip("@").casefold()
        identity = _bot_identity(self.bot_identity)
        return normalized == self.key.value or (identity is not None and normalized == identity)

    def safe_snapshot(self) -> dict[str, object]:
        """Return metadata without secrets, secret references or provider identity."""

        return {
            "key": self.key.value,
            "role": self.role.value,
            "activation": self.activation.value,
            "worker_enabled": self.worker_enabled,
            "effective_capabilities": sorted(
                capability.value for capability in self.effective_capabilities
            ),
            "send_authorizations": sorted(
                authorization.value for authorization in self.send_authorizations
            ),
        }

    def __repr__(self) -> str:
        """Keep provider identity and all secret references out of diagnostics."""

        return (
            "TelegramConnectorDefinition("
            f"key={self.key.value!r}, role={self.role.value!r}, "
            f"activation={self.activation.value!r}, worker_enabled={self.worker_enabled!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class TelegramConnectorRegistry:
    """Closed registry for the current Business and future Alpha connectors."""

    business: TelegramConnectorDefinition
    alpha_group: TelegramConnectorDefinition

    def __post_init__(self) -> None:
        """Require exact connector identities and unique isolation boundaries."""

        if self.business.key is not TelegramConnectorKey.BUSINESS:
            raise ValueError("registry Business slot has the wrong connector")
        if self.alpha_group.key is not TelegramConnectorKey.ALPHA_GROUP:
            raise ValueError("registry Alpha slot has the wrong connector")
        connectors = (self.business, self.alpha_group)
        for attribute in (
            "bot_identity_ref",
            "bot_token_secret_ref",
            "webhook_secret_ref",
            "webhook_path",
        ):
            values = [getattr(connector, attribute) for connector in connectors]
            if len(values) != len(set(values)):
                raise ValueError(f"connector {attribute} values must be unique")
        identities = [
            identity
            for connector in connectors
            if (identity := _bot_identity(connector.bot_identity)) is not None
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("connector bot identities must be unique")
        stable_keys = {connector.key.value for connector in connectors}
        if any(identity in stable_keys for identity in identities):
            raise ValueError("connector bot identity cannot match a stable connector key")

    def require_worker_connector(self, bot_key: str) -> TelegramConnectorDefinition:
        """Resolve only an active, explicitly worker-enabled trusted connector."""

        matched = [
            connector
            for connector in (self.business, self.alpha_group)
            if connector.matches_work_item_key(bot_key)
        ]
        if len(matched) != 1:
            raise TelegramConnectorResolutionError("unknown_telegram_connector")
        connector = matched[0]
        if (
            connector.activation is not TelegramConnectorActivation.ACTIVE
            or not connector.worker_enabled
        ):
            raise TelegramConnectorResolutionError("telegram_connector_disabled")
        return connector

    def safe_snapshot(self) -> dict[str, object]:
        """Return a deterministic redacted registry view."""

        return {
            "connectors": [
                self.business.safe_snapshot(),
                self.alpha_group.safe_snapshot(),
            ]
        }

    def __repr__(self) -> str:
        """Return only the already-redacted connector representations."""

        return (
            "TelegramConnectorRegistry("
            f"business={self.business!r}, alpha_group={self.alpha_group!r})"
        )


def build_telegram_connector_registry(
    *,
    business_bot_username: str,
) -> TelegramConnectorRegistry:
    """Build the release-pinned registry without reading future Alpha secrets."""

    return TelegramConnectorRegistry(
        business=TelegramConnectorDefinition(
            key=TelegramConnectorKey.BUSINESS,
            role=TelegramConnectorRole.BUSINESS,
            activation=TelegramConnectorActivation.ACTIVE,
            bot_identity=business_bot_username,
            bot_identity_ref="TELEGRAM_BOT_USERNAME",
            bot_token_secret_ref="TELEGRAM_BOT_TOKEN",
            webhook_secret_ref="TELEGRAM_WEBHOOK_SECRET",
            webhook_path="/telegram/webhook",
            allowed_updates=BUSINESS_ALLOWED_UPDATES,
            declared_capabilities=frozenset(
                {
                    TelegramConnectorCapability.BOT_MESSAGES,
                    TelegramConnectorCapability.CALLBACK_QUERIES,
                    TelegramConnectorCapability.BUSINESS_CONNECTIONS,
                    TelegramConnectorCapability.BUSINESS_MESSAGES,
                }
            ),
            send_authorizations=frozenset(
                {
                    TelegramSendAuthorization.LEGACY_SYSTEM_REPLY,
                    TelegramSendAuthorization.HUMAN_CONFIRMED,
                }
            ),
            worker_enabled=True,
        ),
        alpha_group=TelegramConnectorDefinition(
            key=TelegramConnectorKey.ALPHA_GROUP,
            role=TelegramConnectorRole.ALPHA_GROUP,
            activation=TelegramConnectorActivation.DORMANT,
            bot_identity=None,
            bot_identity_ref="TELEGRAM_ALPHA_BOT_USERNAME",
            bot_token_secret_ref="TELEGRAM_ALPHA_BOT_TOKEN",
            webhook_secret_ref="TELEGRAM_ALPHA_WEBHOOK_SECRET",
            webhook_path="/telegram/alpha/webhook",
            allowed_updates=ALPHA_GROUP_ALLOWED_UPDATES,
            declared_capabilities=frozenset(
                {TelegramConnectorCapability.GROUP_MESSAGES}
            ),
            send_authorizations=frozenset(),
            worker_enabled=False,
        ),
    )


def _bot_identity(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lstrip("@").casefold()
    return normalized or None
