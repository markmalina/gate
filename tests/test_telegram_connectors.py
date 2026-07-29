"""Contract tests for the trusted Telegram connector registry."""

from dataclasses import replace

import pytest
from app.core.config import Settings
from app.core.telegram_connectors import (
    ALPHA_GROUP_ALLOWED_UPDATES,
    BUSINESS_ALLOWED_UPDATES,
    TelegramConnectorActivation,
    TelegramConnectorCapability,
    TelegramConnectorKey,
    TelegramConnectorRegistry,
    TelegramConnectorResolutionError,
    TelegramConnectorRole,
    TelegramSendAuthorization,
    build_telegram_connector_registry,
)


def _registry(username: str = "gate_m_bot") -> TelegramConnectorRegistry:
    return build_telegram_connector_registry(business_bot_username=username)


def test_registry_preserves_exact_business_transport_contract() -> None:
    connector = _registry().business

    assert connector.key is TelegramConnectorKey.BUSINESS
    assert connector.role is TelegramConnectorRole.BUSINESS
    assert connector.activation is TelegramConnectorActivation.ACTIVE
    assert connector.bot_identity == "gate_m_bot"
    assert connector.bot_identity_ref == "TELEGRAM_BOT_USERNAME"
    assert connector.bot_token_secret_ref == "TELEGRAM_BOT_TOKEN"
    assert connector.webhook_secret_ref == "TELEGRAM_WEBHOOK_SECRET"
    assert connector.webhook_path == "/telegram/webhook"
    assert connector.allowed_updates == BUSINESS_ALLOWED_UPDATES
    assert connector.effective_capabilities == frozenset(
        {
            TelegramConnectorCapability.BOT_MESSAGES,
            TelegramConnectorCapability.CALLBACK_QUERIES,
            TelegramConnectorCapability.BUSINESS_CONNECTIONS,
            TelegramConnectorCapability.BUSINESS_MESSAGES,
        }
    )
    assert connector.send_authorizations == frozenset(
        {
            TelegramSendAuthorization.LEGACY_SYSTEM_REPLY,
            TelegramSendAuthorization.HUMAN_CONFIRMED,
        }
    )
    assert connector.worker_enabled is True


def test_alpha_definition_is_separate_and_runtime_dormant() -> None:
    connector = _registry().alpha_group

    assert connector.key is TelegramConnectorKey.ALPHA_GROUP
    assert connector.role is TelegramConnectorRole.ALPHA_GROUP
    assert connector.activation is TelegramConnectorActivation.DORMANT
    assert connector.bot_identity is None
    assert connector.bot_identity_ref == "TELEGRAM_ALPHA_BOT_USERNAME"
    assert connector.bot_token_secret_ref == "TELEGRAM_ALPHA_BOT_TOKEN"
    assert connector.webhook_secret_ref == "TELEGRAM_ALPHA_WEBHOOK_SECRET"
    assert connector.webhook_path == "/telegram/alpha/webhook"
    assert connector.allowed_updates == ALPHA_GROUP_ALLOWED_UPDATES
    assert connector.declared_capabilities == frozenset(
        {TelegramConnectorCapability.GROUP_MESSAGES}
    )
    assert connector.effective_capabilities == frozenset()
    assert connector.send_authorizations == frozenset()
    assert connector.worker_enabled is False


def test_beta_definition_is_separate_and_runtime_dormant() -> None:
    connector = _registry().beta_group

    assert connector.key is TelegramConnectorKey.BETA_GROUP
    assert connector.role is TelegramConnectorRole.ALPHA_GROUP
    assert connector.activation is TelegramConnectorActivation.DORMANT
    assert connector.bot_identity is None
    assert connector.bot_identity_ref == "TELEGRAM_BETA_BOT_USERNAME"
    assert connector.bot_token_secret_ref == "TELEGRAM_BETA_BOT_TOKEN"
    assert connector.webhook_secret_ref == "TELEGRAM_BETA_WEBHOOK_SECRET"
    assert connector.webhook_path == "/telegram/beta/webhook"
    assert connector.allowed_updates == ALPHA_GROUP_ALLOWED_UPDATES
    assert connector.send_authorizations == frozenset()
    assert connector.worker_enabled is False


def test_enabled_beta_resolves_only_its_own_identity_and_key() -> None:
    registry = build_telegram_connector_registry(
        business_bot_username="gate_m_bot",
        beta_enabled=True,
        beta_bot_username="BetaViaBot",
    )

    assert registry.require_worker_connector("BetaViaBot").key is TelegramConnectorKey.BETA_GROUP
    assert (
        registry.require_worker_connector("telegram_beta_group").key
        is TelegramConnectorKey.BETA_GROUP
    )
    with pytest.raises(TelegramConnectorResolutionError):
        registry.require_worker_connector("AlphaViaBot")


def test_future_alpha_environment_cannot_activate_or_project_into_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_ALPHA_BOT_TOKEN", "must-not-be-read")
    monkeypatch.setenv("TELEGRAM_ALPHA_BOT_USERNAME", "must_not_activate_bot")
    monkeypatch.setenv("TELEGRAM_ALPHA_WEBHOOK_SECRET", "must-not-be-read")
    monkeypatch.setenv("GATE_TELEGRAM_ALPHA_ENABLED", "true")

    registry = Settings(_env_file=None).telegram_connectors
    connector = registry.alpha_group
    wire = f"{registry!r} {registry.safe_snapshot()}"

    assert connector.activation is TelegramConnectorActivation.DORMANT
    assert connector.bot_identity is None
    assert connector.effective_capabilities == frozenset()
    assert "must-not-be-read" not in wire
    assert "must_not_activate_bot" not in wire


@pytest.mark.parametrize(
    ("bot_key", "expected_key"),
    [
        ("gate_m_bot", TelegramConnectorKey.BUSINESS),
        ("@Gate_M_Bot", TelegramConnectorKey.BUSINESS),
        ("telegram_business", TelegramConnectorKey.BUSINESS),
    ],
)
def test_worker_resolution_accepts_only_explicit_business_keys(
    bot_key: str,
    expected_key: TelegramConnectorKey,
) -> None:
    connector = _registry().require_worker_connector(bot_key)

    assert connector.key is expected_key
    assert connector.role is TelegramConnectorRole.BUSINESS


def test_custom_business_identity_replaces_the_legacy_username() -> None:
    registry = _registry("new_business_bot")

    assert registry.require_worker_connector("new_business_bot").key is (
        TelegramConnectorKey.BUSINESS
    )
    with pytest.raises(TelegramConnectorResolutionError) as captured:
        registry.require_worker_connector("gate_m_bot")
    assert captured.value.code == "unknown_telegram_connector"


@pytest.mark.parametrize(
    ("bot_key", "error_code"),
    [
        ("unknown_bot", "unknown_telegram_connector"),
        ("telegram_alpha_group", "telegram_connector_disabled"),
        ("telegram_beta_group", "telegram_connector_disabled"),
    ],
)
def test_worker_resolution_fails_closed_for_unknown_or_dormant_connector(
    bot_key: str,
    error_code: str,
) -> None:
    with pytest.raises(TelegramConnectorResolutionError) as captured:
        _registry().require_worker_connector(bot_key)

    assert captured.value.code == error_code
    assert str(captured.value) == error_code


@pytest.mark.parametrize(
    "capability",
    [
        TelegramConnectorCapability.BUSINESS_MESSAGES,
        TelegramConnectorCapability.CALLBACK_QUERIES,
    ],
)
def test_registry_rejects_non_group_alpha_capability(
    capability: TelegramConnectorCapability,
) -> None:
    registry = _registry()

    with pytest.raises(ValueError, match="capabilities are release-pinned"):
        replace(
            registry.alpha_group,
            declared_capabilities=frozenset({capability}),
        )


@pytest.mark.parametrize("update_kind", ["business_message", "callback_query", "channel_post"])
def test_registry_rejects_non_group_alpha_update_shape(update_kind: str) -> None:
    registry = _registry()

    with pytest.raises(ValueError, match="update allowlist is release-pinned"):
        replace(
            registry.alpha_group,
            allowed_updates=("message", update_kind),
        )


def test_registry_rejects_business_agent_delivery_authority() -> None:
    registry = _registry()

    with pytest.raises(ValueError, match="no agent delivery authority"):
        replace(
            registry.business,
            send_authorizations=frozenset(
                {TelegramSendAuthorization.AGENT_DELIVERY_GRANT}
            ),
        )


def test_registry_rejects_identity_that_matches_a_stable_connector_key() -> None:
    with pytest.raises(ValueError, match="cannot match a stable connector key"):
        _registry("telegram_alpha_group")


@pytest.mark.parametrize(
    "attribute",
    [
        "bot_identity_ref",
        "bot_token_secret_ref",
        "webhook_secret_ref",
        "webhook_path",
    ],
)
def test_registry_rejects_shared_connector_boundaries(attribute: str) -> None:
    registry = _registry()
    duplicated_alpha = replace(
        registry.alpha_group,
        **{attribute: getattr(registry.business, attribute)},
    )

    with pytest.raises(ValueError, match="must be unique"):
        TelegramConnectorRegistry(
            business=registry.business,
            alpha_group=duplicated_alpha,
            beta_group=registry.beta_group,
        )


def test_repr_and_safe_snapshot_omit_identity_and_secret_references() -> None:
    registry = _registry("private-provider-identity")

    wire = f"{registry!r} {registry.safe_snapshot()}"

    assert "private-provider-identity" not in wire
    assert "TELEGRAM_BOT_USERNAME" not in wire
    assert "TELEGRAM_BOT_TOKEN" not in wire
    assert "TELEGRAM_WEBHOOK_SECRET" not in wire
    assert "TELEGRAM_ALPHA_BOT_USERNAME" not in wire
    assert "TELEGRAM_ALPHA_BOT_TOKEN" not in wire
    assert "TELEGRAM_ALPHA_WEBHOOK_SECRET" not in wire
    assert "TELEGRAM_BETA_BOT_USERNAME" not in wire
    assert "TELEGRAM_BETA_BOT_TOKEN" not in wire
    assert "TELEGRAM_BETA_WEBHOOK_SECRET" not in wire
    assert "/telegram/webhook" not in wire
