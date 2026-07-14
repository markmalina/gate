"""Characterize the typed Business connector against live transport wiring."""

from app.api.telegram_ingress import TELEGRAM_WEBHOOK_PATH
from app.core.telegram_connectors import build_telegram_connector_registry
from app.services.telegram_webhook import TELEGRAM_ALLOWED_UPDATES


def test_business_connector_matches_live_webhook_path_and_update_allowlist() -> None:
    connector = build_telegram_connector_registry(
        business_bot_username="gate_m_bot"
    ).business

    assert connector.webhook_path == TELEGRAM_WEBHOOK_PATH
    assert connector.allowed_updates == tuple(TELEGRAM_ALLOWED_UPDATES)
