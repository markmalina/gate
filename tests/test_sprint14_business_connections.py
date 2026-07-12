"""Sprint 14 Telegram Business connection routing tests."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api.sprint14_routes import (
    _account_id_from_configured_connection,
    _is_business_send_enabled,
    _is_business_update,
    _is_outbound_business_message,
    _resolve_account_id,
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
    assert _is_business_update({"message": {"business_connection_id": "bc-1"}})
    assert not _is_business_update({"message": {"text": "ping"}})


def test_business_message_direction_uses_all_owner_identities() -> None:
    owner_identity_ids = frozenset({"7819816320", "8648171437"})

    assert _is_outbound_business_message(
        sender_id="7819816320",
        owner_identity_ids=owner_identity_ids,
    )
    assert _is_outbound_business_message(
        sender_id="8648171437",
        owner_identity_ids=owner_identity_ids,
    )
    assert not _is_outbound_business_message(
        sender_id="203349059",
        owner_identity_ids=owner_identity_ids,
    )
    assert not _is_outbound_business_message(
        sender_id="7819816320",
        owner_identity_ids=frozenset(),
    )


@pytest.mark.asyncio
async def test_unknown_business_connection_fails_closed() -> None:
    settings = Settings(
        GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID="",
        GATE_ACCOUNT_BY_BUSINESS_CONNECTION_ID="",
    )
    session = AsyncMock()
    result = Mock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_account_id(
            session=session,
            business_connection_id="unknown-connection",
            settings=settings,
        )

    assert exc_info.value.status_code == 409
    assert "Unknown Telegram Business connection" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_existing_business_connection_keeps_its_account() -> None:
    settings = Settings(
        GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID="",
        GATE_ACCOUNT_BY_BUSINESS_CONNECTION_ID="",
    )
    session = AsyncMock()
    result = Mock()
    connection = Mock(account_id=TELEGRAM_MARK_BY_ACCOUNT_ID)
    result.scalar_one_or_none.return_value = connection
    session.execute.return_value = result

    assert (
        await _resolve_account_id(
            session=session,
            business_connection_id="known-connection",
            settings=settings,
        )
        == TELEGRAM_MARK_BY_ACCOUNT_ID
    )
