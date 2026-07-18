"""Sprint 14 Telegram Business connection routing tests."""

import asyncio
import inspect

from app.api.sprint14_routes import (
    _account_id_from_configured_connection,
    _find_receiving_account_identity_ids,
    _is_business_send_enabled,
    _is_business_update,
    _is_outbound_business_message,
    _resolve_account_id,
    _resolve_business_connection_account_id,
    _upsert_telegram_counterparty,
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


def test_business_message_direction_uses_receiving_account_identities() -> None:
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


def test_owner_identity_lookup_is_receiving_account_relative() -> None:
    source = inspect.getsource(_find_receiving_account_identity_ids)

    assert "Account.id == account_id" in source
    assert "Account.owner == owner" not in source


class _Result:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value

    def one_or_none(self) -> object | None:
        return self._value


class _Session:
    def __init__(self, value: object | None) -> None:
        self._value = value

    async def execute(self, _statement: object) -> _Result:
        return _Result(self._value)


class _SequenceSession:
    def __init__(self, values: list[object | None]) -> None:
        self._values = iter(values)

    async def execute(self, _statement: object) -> _Result:
        return _Result(next(self._values))


class _Connection:
    def __init__(self, account_id: object) -> None:
        self.account_id = account_id


class _Account:
    def __init__(self, account_id: object) -> None:
        self.id = account_id


class _Identity:
    def __init__(self) -> None:
        self.username = "old_username"
        self.display_name = "Profile name"
        self.metadata_ = {
            "name_source": "telegram_contact",
            "telegram_contact_name": "Женя — зал",
        }


class _Person:
    def __init__(self) -> None:
        self.id = "person-id"
        self.display_name = "Женя — зал"


def test_profile_updates_do_not_overwrite_saved_telegram_contact_name() -> None:
    identity = _Identity()
    person = _Person()

    person_id, display_name = asyncio.run(
        _upsert_telegram_counterparty(
            session=_Session((identity, person)),  # type: ignore[arg-type]
            account_id=TELEGRAM_MARK_BY_ACCOUNT_ID,
            update={
                "business_message": {
                    "from": {
                        "id": 12345,
                        "first_name": "Evgeniy",
                        "username": "new_username",
                    }
                }
            },
            is_outbound=False,
            fallback_user_id="12345",
        )
    )

    assert person_id == "person-id"
    assert display_name == "Женя — зал"
    assert person.display_name == "Женя — зал"
    assert identity.display_name == "Evgeniy"
    assert identity.username == "new_username"
    assert identity.metadata_["telegram_profile_name"] == "Evgeniy"
    assert identity.metadata_["name_source"] == "telegram_contact"


def test_new_business_connection_resolves_from_telegram_owner() -> None:
    settings = Settings(
        GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID="",
        GATE_ACCOUNT_BY_BUSINESS_CONNECTION_ID="",
    )

    resolved = asyncio.run(
        _resolve_business_connection_account_id(
            session=_SequenceSession(  # type: ignore[arg-type]
                [None, _Account(TELEGRAM_MARK_BY_ACCOUNT_ID)]
            ),
            update={
                "business_connection": {
                    "id": "new-by-connection",
                    "user": {"id": 8648171437},
                    "is_enabled": True,
                    "rights": {"can_reply": True},
                }
            },
            business_connection_id="new-by-connection",
            settings=settings,
        )
    )

    assert resolved == TELEGRAM_MARK_BY_ACCOUNT_ID


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
