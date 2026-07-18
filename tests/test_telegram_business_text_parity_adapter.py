"""Read-only adapter tests for Business text legacy parity."""

from __future__ import annotations

from typing import Any
from uuid import UUID

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
    def __init__(
        self,
        rows: list[tuple[object, ...]],
        *,
        reject_evidence_scope: bool = False,
    ) -> None:
        self.rows = rows
        self.reject_evidence_scope = reject_evidence_scope
        self.statements: list[Any] = []
        self.execute_count = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        self.execute_count += 1
        return FakeResult(self.rows if self.execute_count == 1 else [])

    async def scalar(self, statement: Any) -> UUID | None:
        self.statements.append(statement)
        if self.reject_evidence_scope and str(statement).startswith("INSERT INTO"):
            return None
        return UUID(
            "11111111-1111-1111-1111-111111111111"
            if len(self.statements) == 2
            else "22222222-2222-2222-2222-222222222222"
        )


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def begin(self) -> FakeSession:
        return self.session


def _command() -> TelegramBusinessTextParityCommand:
    return TelegramBusinessTextParityCommand(
        workspace_key="mark-personal",
        account_id="22222222-2222-2222-2222-222222222222",
        canonical_message_id="44444444-4444-4444-4444-444444444444",
        update_id=42,
        account_alias="telegram_mark_ru",
        direction=CanonicalMessageDirection.INBOUND,
        route=WorkRoute.HUMAN_REPLY_TASK,
        provider_dialog_id="private-dialog",
        business_connection_id="private-connection",
        text_content="private-text",
    )


@pytest.mark.anyio
async def test_adapter_records_matched_content_free_evidence() -> None:
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
    assert len(session.statements) == 4
    statement = str(session.statements[0])
    assert "messages.telegram_update_id" in statement
    assert "reply_tasks.trigger_message_id" in statement
    evidence_statement = str(session.statements[-1])
    assert "INSERT INTO telegram_business_text_parity_evidence" in evidence_statement
    assert "private-text" not in statement
    assert "private-text" not in evidence_statement


@pytest.mark.anyio
async def test_adapter_retries_when_legacy_transaction_is_not_committed_yet() -> None:
    session = FakeSession([])
    observer = SessionFactoryTelegramBusinessTextParityObserver(
        FakeSessions(session)  # type: ignore[arg-type]
    )

    with pytest.raises(
        TelegramBusinessTextParityError,
        match="telegram_business_text_parity_legacy_pending",
    ):
        await observer.observe(_command())

    evidence_statement = str(session.statements[-1])
    assert "INSERT INTO telegram_business_text_parity_evidence" in evidence_statement
    assert "telegram_business_text_parity_legacy_pending" not in evidence_statement
    assert "private-text" not in evidence_statement


@pytest.mark.anyio
async def test_adapter_fails_closed_when_evidence_scope_conflicts() -> None:
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
        ],
        reject_evidence_scope=True,
    )
    observer = SessionFactoryTelegramBusinessTextParityObserver(
        FakeSessions(session)  # type: ignore[arg-type]
    )

    with pytest.raises(
        TelegramBusinessTextParityError,
        match="telegram_business_text_parity_scope_invalid",
    ):
        await observer.observe(_command())
