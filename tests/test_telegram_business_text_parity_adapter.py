"""Read-only adapter tests for Business text legacy parity."""

from __future__ import annotations

from typing import Any

import pytest
from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    WorkRoute,
)
from app.application.telegram_business_text_parity import (
    TelegramBusinessTextParityCommand,
    TelegramBusinessTextParityError,
)
from app.infrastructure.telegram_business_text_parity import (
    SessionFactoryTelegramBusinessTextParityObserver,
)


class FakeResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.rows)


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


def _command() -> TelegramBusinessTextParityCommand:
    return TelegramBusinessTextParityCommand(
        update_id=42,
        account_alias="telegram_mark_ru",
        direction=CanonicalMessageDirection.INBOUND,
        route=WorkRoute.HUMAN_REPLY_TASK,
        provider_dialog_id="private-dialog",
        business_connection_id="private-connection",
        text_content="private-text",
    )


@pytest.mark.anyio
async def test_adapter_reads_one_exact_legacy_fact_without_writes() -> None:
    session = FakeSession(
        [
            (
                "telegram_mark_ru",
                "inbound",
                "private-dialog",
                "private-connection",
                "private-text",
                1,
            )
        ]
    )
    observer = SessionFactoryTelegramBusinessTextParityObserver(
        FakeSessions(session)  # type: ignore[arg-type]
    )

    result = await observer.observe(_command())

    assert result.disposition == "matched"
    assert len(session.statements) == 1
    statement = str(session.statements[0])
    assert "messages.telegram_update_id" in statement
    assert "reply_tasks.trigger_message_id" in statement
    assert "INSERT" not in statement
    assert "UPDATE" not in statement
    assert "private-text" not in statement


@pytest.mark.anyio
async def test_adapter_retries_when_legacy_transaction_is_not_committed_yet() -> None:
    observer = SessionFactoryTelegramBusinessTextParityObserver(
        FakeSessions(FakeSession([]))  # type: ignore[arg-type]
    )

    with pytest.raises(
        TelegramBusinessTextParityError,
        match="telegram_business_text_parity_legacy_pending",
    ):
        await observer.observe(_command())
