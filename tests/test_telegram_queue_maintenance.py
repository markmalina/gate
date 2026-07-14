"""Application guards for bounded stale Telegram claim recovery."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.application.telegram_queue_maintenance import (
    RecoverStaleTelegramClaimsCommand,
    RecoverStaleTelegramClaimsService,
    TelegramStaleClaimRecovery,
)


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


def _command(**overrides: object) -> RecoverStaleTelegramClaimsCommand:
    recovered_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    values = {
        "stale_before": recovered_at - timedelta(minutes=5),
        "recovered_at": recovered_at,
        "max_attempts": 5,
        "limit": 100,
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


@pytest.mark.parametrize(
    "command",
    [
        _command(
            stale_before=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        ),
        _command(max_attempts=0),
        _command(limit=0),
        _command(limit=1001),
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
