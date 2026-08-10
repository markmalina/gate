"""Application guards for bounded stale Telegram claim recovery."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.application.telegram_queue_maintenance import (
    RecoverStaleTelegramClaimsCommand,
    RecoverStaleTelegramClaimsService,
    TelegramStaleClaimRecovery,
)
from app.infrastructure.telegram_queue_maintenance import SqlTelegramQueueMaintenance


class RecordingMaintenance:
    def __init__(self) -> None:
        self.commands: list[RecoverStaleTelegramClaimsCommand] = []

    async def recover_stale(
        self,
        command: RecoverStaleTelegramClaimsCommand,
    ) -> TelegramStaleClaimRecovery:
        self.commands.append(command)
        return TelegramStaleClaimRecovery(
            recovered=0,
            retry_scheduled=0,
            dead_lettered=0,
        )


class EmptyRows:
    def all(self) -> list[tuple[object, object]]:
        return []


class RecordingSession:
    def __init__(self) -> None:
        self.statements: list[object] = []
        self.commits = 0

    async def execute(self, statement: object) -> EmptyRows:
        self.statements.append(statement)
        return EmptyRows()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        raise AssertionError("valid empty recovery must not roll back")

    def add(self, _value: object) -> None:
        raise AssertionError("empty recovery must not write an audit row")


def _command(**overrides: object) -> RecoverStaleTelegramClaimsCommand:
    recovered_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    values = {
        "stale_before": recovered_at - timedelta(minutes=5),
        "recovered_at": recovered_at,
        "max_attempts": 5,
        "limit": 100,
        "require_non_voice_payload": True,
        **overrides,
    }
    return RecoverStaleTelegramClaimsCommand(**values)


def test_valid_recovery_is_delegated_once() -> None:
    maintenance = RecordingMaintenance()

    result = asyncio.run(
        RecoverStaleTelegramClaimsService(maintenance).execute(_command())
    )

    assert result.recovered == 0
    assert len(maintenance.commands) == 1


def test_non_voice_sql_scope_is_inbound_only() -> None:
    session = RecordingSession()

    result = asyncio.run(
        SqlTelegramQueueMaintenance(session).recover_stale(  # type: ignore[arg-type]
            _command()
        )
    )

    assert result == TelegramStaleClaimRecovery(
        recovered=0,
        retry_scheduled=0,
        dead_lettered=0,
    )
    assert session.commits == 1
    assert len(session.statements) == 1
    statement = str(session.statements[0])
    assert "telegram_outbox_events" in statement
    assert "telegram_receipts" in statement
    assert "send_attempts" not in statement
    assert "agent_send_attempts" not in statement
    assert "delivery_commands" not in statement


@pytest.mark.parametrize(
    "command",
    [
        _command(
            stale_before=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        ),
        _command(max_attempts=0),
        _command(limit=0),
        _command(limit=1001),
        _command(require_non_voice_payload=False),
        _command(require_persisted_voice_transcript=True),
        _command(
            require_non_voice_payload=False,
            require_persisted_voice_transcript=True,
            require_missing_voice_archive=True,
        ),
    ],
)
def test_invalid_recovery_bounds_fail_before_adapter(
    command: RecoverStaleTelegramClaimsCommand,
) -> None:
    maintenance = RecordingMaintenance()

    with pytest.raises(ValueError):
        asyncio.run(
            RecoverStaleTelegramClaimsService(maintenance).execute(command)
        )

    assert maintenance.commands == []
