"""Sprint 14 Telegram Business connection routing tests."""

import asyncio

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


class _Result:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _Session:
    def __init__(self, value: object | None) -> None:
        self._value = value

    async def execute(self, _statement: object) -> _Result:
        return _Result(self._value)


class _Connection:
    def __init__(self, account_id: object) -> None:
        self.account_id = account_id


def test_unknown_business_connection_fails_closed() -> None:
    settings = Settings(
        GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID="",
        GATE_ACCOUNT_BY_BUSINESS_CONNECTION_ID="",
    )
    try:
        asyncio.run(
            _resolve_account_id(
                session=_Session(None),  # type: ignore[arg-type]
                business_connection_id="unknown-connection",
                settings=settings,
            )
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "Unknown Telegram Business connection" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("Unknown Business connections must fail closed")


def test_existing_business_connection_keeps_its_account() -> None:
    settings = Settings(
        GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID="",
        GATE_ACCOUNT_BY_BUSINESS_CONNECTION_ID="",
    )
    assert (
        asyncio.run(
            _resolve_account_id(
                session=_Session(_Connection(TELEGRAM_MARK_BY_ACCOUNT_ID)),  # type: ignore[arg-type]
                business_connection_id="known-connection",
                settings=settings,
            )
        )
        == TELEGRAM_MARK_BY_ACCOUNT_ID
    )
