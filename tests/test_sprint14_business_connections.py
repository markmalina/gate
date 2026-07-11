"""Sprint 14 Telegram Business connection routing tests."""

from app.api.sprint14_routes import (
    _account_id_from_configured_connection,
    _is_business_send_enabled,
    _is_business_update,
)
from app.core.config import Settings
from app.models.domain import TELEGRAM_MARK_BY_ACCOUNT_ID, TELEGRAM_MARK_RU_ACCOUNT_ID


def test_business_connection_ids_resolve_to_configured_accounts() -> None:
    settings = Settings(
        GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID="ru-connection",
        GATE_ACCOUNT_BY_BUSINESS_CONNECTION_ID="by-connection",
    )

    assert (
        _account_id_from_configured_connection("ru-connection", settings=settings)
        == TELEGRAM_MARK_RU_ACCOUNT_ID
    )
    assert (
        _account_id_from_configured_connection("by-connection", settings=settings)
        == TELEGRAM_MARK_BY_ACCOUNT_ID
    )
    assert _account_id_from_configured_connection("unknown", settings=settings) is None
    assert _account_id_from_configured_connection(None, settings=settings) is None


def test_business_send_kill_switches_are_account_scoped() -> None:
    globally_disabled = Settings(GATE_SEND_ENABLED=False)
    ru_disabled = Settings(GATE_ACCOUNT_RU_SEND_ENABLED=False)
    by_disabled = Settings(GATE_ACCOUNT_BY_SEND_ENABLED=False)

    assert not _is_business_send_enabled(
        account_id=TELEGRAM_MARK_RU_ACCOUNT_ID,
        settings=globally_disabled,
    )
    assert not _is_business_send_enabled(
        account_id=TELEGRAM_MARK_RU_ACCOUNT_ID,
        settings=ru_disabled,
    )
    assert _is_business_send_enabled(
        account_id=TELEGRAM_MARK_BY_ACCOUNT_ID,
        settings=ru_disabled,
    )
    assert not _is_business_send_enabled(
        account_id=TELEGRAM_MARK_BY_ACCOUNT_ID,
        settings=by_disabled,
    )


def test_business_update_detection_is_limited_to_telegram_business_shapes() -> None:
    assert _is_business_update({"business_connection": {"id": "bc-1"}})
    assert _is_business_update({"business_message": {"business_connection_id": "bc-1"}})
    assert not _is_business_update({"message": {"text": "ping"}})
