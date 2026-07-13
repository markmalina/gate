"""Tests for the isolated Telegram ingress identity read model."""

import asyncio
import inspect

from app.application.telegram_ingress_identity import (
    ResolveTelegramIngressIdentityCommand,
    ResolveTelegramIngressIdentityService,
    TelegramIngressIdentitySnapshot,
)
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
            )
        )
    )

    assert result.resolved is False
    assert result.direction == "unresolved"
    assert result.reply_task_intent == "unresolved"
    assert result.resolution == "connection_not_found"


def test_sql_identity_adapter_has_no_write_or_transaction_methods() -> None:
    source = inspect.getsource(SqlTelegramIngressIdentityReader)

    assert "select(" in source
    assert ".add(" not in source
    assert ".flush(" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
