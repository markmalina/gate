"""Tests for the isolated Telegram ingress identity read model."""

import asyncio
import inspect

from app.application.telegram_ingress_identity import (
    ResolveTelegramIngressIdentityCommand,
    ResolveTelegramIngressIdentityService,
    TelegramIdentityResolutionCode,
    TelegramIngressDirection,
    TelegramIngressIdentitySnapshot,
    TelegramIngressSenderKind,
    TelegramReplyTaskIntent,
    build_telegram_business_identity_command,
    direction_evidence_from_identity,
    direction_evidence_from_telegram_message,
    project_telegram_sender_identity,
)
from app.application.work_ownership import DirectionEvidence
from app.infrastructure.telegram_ingress_identity import SqlTelegramIngressIdentityReader


class FakeIdentityReader:
    def __init__(self, snapshot: TelegramIngressIdentitySnapshot | None) -> None:
        self.snapshot = snapshot
        self.requested_connection_id: str | None = None

    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        self.requested_connection_id = business_connection_id
        return self.snapshot


def _snapshot() -> TelegramIngressIdentitySnapshot:
    return TelegramIngressIdentitySnapshot(
        account_id="account-ru",
        account_alias="telegram_mark_ru",
        project_id="project-personal",
        owner_identity_ids=frozenset({"owner-1", "owner-2"}),
    )


def test_owner_sender_resolves_outbound_and_satisfies_existing_tasks() -> None:
    reader = FakeIdentityReader(_snapshot())
    service = ResolveTelegramIngressIdentityService(reader)

    result = asyncio.run(
        service.execute(
            ResolveTelegramIngressIdentityCommand(
                business_connection_id="connection-ru",
                sender_id="owner-2",
                text_present=True,
                sender_kind=TelegramIngressSenderKind.HUMAN,
            )
        )
    )

    assert reader.requested_connection_id == "connection-ru"
    assert result.resolved is True
    assert result.direction == "outbound"
    assert result.reply_task_intent == "satisfy_existing"
    assert result.account_alias == "telegram_mark_ru"


def test_non_owner_text_resolves_inbound_reply_task_creation() -> None:
    service = ResolveTelegramIngressIdentityService(FakeIdentityReader(_snapshot()))

    result = asyncio.run(
        service.execute(
            ResolveTelegramIngressIdentityCommand(
                business_connection_id="connection-ru",
                sender_id="person-42",
                text_present=True,
                sender_kind=TelegramIngressSenderKind.HUMAN,
            )
        )
    )

    assert result.direction == "inbound"
    assert result.reply_task_intent == "create_inbound_human_text"
    assert result.project_id == "project-personal"


def test_non_text_inbound_message_does_not_create_reply_task() -> None:
    service = ResolveTelegramIngressIdentityService(FakeIdentityReader(_snapshot()))

    result = asyncio.run(
        service.execute(
            ResolveTelegramIngressIdentityCommand(
                business_connection_id="connection-ru",
                sender_id="person-42",
                text_present=False,
                sender_kind=TelegramIngressSenderKind.HUMAN,
            )
        )
    )

    assert result.direction == "inbound"
    assert result.reply_task_intent == "none"


def test_unknown_connection_remains_explicitly_unresolved() -> None:
    service = ResolveTelegramIngressIdentityService(FakeIdentityReader(None))

    result = asyncio.run(
        service.execute(
            ResolveTelegramIngressIdentityCommand(
                business_connection_id="unknown",
                sender_id="person-42",
                text_present=True,
                sender_kind=TelegramIngressSenderKind.HUMAN,
            )
        )
    )

    assert result.resolved is False
    assert result.direction == "unresolved"
    assert result.reply_task_intent == "unresolved"
    assert result.resolution == "connection_not_found"


def test_empty_owner_identity_set_never_promotes_non_owner_to_human_inbound() -> None:
    snapshot = TelegramIngressIdentitySnapshot(
        account_id="account-ru",
        account_alias="telegram_mark_ru",
        project_id="project-personal",
        owner_identity_ids=frozenset(),
    )
    service = ResolveTelegramIngressIdentityService(FakeIdentityReader(snapshot))

    result = asyncio.run(
        service.execute(
            ResolveTelegramIngressIdentityCommand(
                business_connection_id="connection-ru",
                sender_id="person-42",
                text_present=True,
                sender_kind=TelegramIngressSenderKind.HUMAN,
            )
        )
    )

    assert result.resolved is True
    assert result.direction is TelegramIngressDirection.UNRESOLVED
    assert result.reply_task_intent is TelegramReplyTaskIntent.UNRESOLVED
    assert result.resolution is TelegramIdentityResolutionCode.OWNER_IDENTITY_NOT_FOUND
    assert direction_evidence_from_identity(result) is DirectionEvidence.UNKNOWN


def test_bot_or_service_sender_never_creates_a_reply_task_intent() -> None:
    service = ResolveTelegramIngressIdentityService(FakeIdentityReader(_snapshot()))

    result = asyncio.run(
        service.execute(
            ResolveTelegramIngressIdentityCommand(
                business_connection_id="connection-ru",
                sender_id="service-42",
                text_present=True,
                sender_kind=TelegramIngressSenderKind.BOT_OR_SERVICE,
            )
        )
    )

    assert result.direction is TelegramIngressDirection.BOT_OR_SERVICE
    assert result.reply_task_intent is TelegramReplyTaskIntent.NONE
    assert direction_evidence_from_identity(result) is DirectionEvidence.BOT_OR_SERVICE


def test_sender_projection_is_fail_closed_for_service_and_malformed_shapes() -> None:
    human = project_telegram_sender_identity({"from": {"id": 42, "is_bot": False}})
    bot = project_telegram_sender_identity({"from": {"id": 43, "is_bot": True}})
    service = project_telegram_sender_identity(
        {
            "from": {"id": 44, "is_bot": False},
            "sender_chat": {"id": -1001},
        }
    )

    assert human.kind is TelegramIngressSenderKind.HUMAN
    assert human.sender_id == "42"
    assert bot.kind is TelegramIngressSenderKind.BOT_OR_SERVICE
    assert service.kind is TelegramIngressSenderKind.BOT_OR_SERVICE
    for malformed in (
        {},
        {"from": {"id": True, "is_bot": False}},
        {"from": {"id": 0, "is_bot": False}},
        {"from": {"id": 1 << 52, "is_bot": False}},
        {"from": {"id": -(1 << 52), "is_bot": False}},
        {"from": {"id": "45", "is_bot": False}},
        {"from": {"id": [], "is_bot": False}},
        {"from": {"id": 45}},
    ):
        assert project_telegram_sender_identity(malformed).kind is TelegramIngressSenderKind.UNKNOWN

    negative_service = project_telegram_sender_identity({"sender_chat": {"id": -1001}})
    assert negative_service.sender_id == "-1001"
    assert negative_service.kind is TelegramIngressSenderKind.BOT_OR_SERVICE


def test_business_command_accepts_both_production_and_legacy_private_shapes() -> None:
    for message_key in ("business_message", "message"):
        command = build_telegram_business_identity_command(
            {
                "update_id": 42,
                message_key: {
                    "business_connection_id": "connection-ru",
                    "chat": {"id": 101, "type": "private"},
                    "from": {"id": 202, "is_bot": False},
                    "text": "hello",
                },
            }
        )

        assert command is not None
        assert command.business_connection_id == "connection-ru"
        assert command.sender_id == "202"
        assert command.sender_kind is TelegramIngressSenderKind.HUMAN
        assert command.text_present is True


def test_identity_lookup_is_not_built_for_control_group_or_malformed_shapes() -> None:
    cases = (
        {"update_id": 1, "business_connection": {"id": "connection-ru"}},
        {
            "update_id": 2,
            "business_message": {
                "business_connection_id": "connection-ru",
                "chat": {"id": -1001, "type": "group"},
                "from": {"id": 202, "is_bot": False},
            },
        },
        {
            "update_id": 3,
            "business_message": {
                "business_connection_id": "connection-ru",
                "chat": {"id": 101, "type": "private"},
                "from": {"id": True, "is_bot": False},
            },
        },
        {
            "update_id": 4,
            "business_message": {},
            "message": {},
        },
        {"update_id": 5, "edited_business_message": {}},
        {"update_id": 6, "deleted_business_messages": {}},
    )

    assert all(build_telegram_business_identity_command(case) is None for case in cases)


def test_group_invocation_direction_uses_sender_shape_without_identity_lookup() -> None:
    human_group = {
        "message": {
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 202, "is_bot": False},
            "text": "/ask@alpha_bot hello",
        }
    }
    service_group = {
        "message": {
            "chat": {"id": -1001, "type": "supergroup"},
            "sender_chat": {"id": -1001},
            "text": "/ask@alpha_bot hello",
        }
    }

    assert (
        direction_evidence_from_telegram_message(human_group)
        is DirectionEvidence.HUMAN_INBOUND
    )
    assert (
        direction_evidence_from_telegram_message(service_group)
        is DirectionEvidence.BOT_OR_SERVICE
    )


def test_sql_identity_adapter_has_no_write_or_transaction_methods() -> None:
    source = inspect.getsource(SqlTelegramIngressIdentityReader)

    assert "select(" in source
    assert source.count("await self._session.execute(") == 1
    assert ".add(" not in source
    assert ".flush(" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".with_for_update(" not in source


def test_identity_contract_repr_excludes_provider_and_owner_identity_sentinels() -> None:
    snapshot = TelegramIngressIdentitySnapshot(
        account_id="private-account-id",
        account_alias="telegram_mark_ru",
        project_id="private-project-id",
        owner_identity_ids=frozenset({"private-owner-id"}),
    )
    command = ResolveTelegramIngressIdentityCommand(
        business_connection_id="private-connection-id",
        sender_id="private-sender-id",
        text_present=True,
        sender_kind=TelegramIngressSenderKind.HUMAN,
    )
    resolution = asyncio.run(
        ResolveTelegramIngressIdentityService(FakeIdentityReader(snapshot)).execute(command)
    )

    rendered = f"{snapshot!r} {command!r} {resolution!r}"
    for sentinel in (
        "private-account-id",
        "private-project-id",
        "private-owner-id",
        "private-connection-id",
        "private-sender-id",
    ):
        assert sentinel not in rendered
