"""Safety guards for bounded database-only reply-send recovery."""

import ast
import asyncio
import logging
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application import reply_send_recovery as application_module
from app.application.reply_send_recovery import (
    RecoverStaleReplySendsCommand,
    RecoverStaleReplySendsService,
    ReplySendRecoveryResult,
)
from app.core.config import Settings
from app.infrastructure import reply_send_recovery as infrastructure_module
from app.worker import reply_send_recovery as worker_module
from app.worker.reply_send_recovery import run_reply_send_recovery_once
from pydantic import ValidationError


class RecordingRecovery:
    def __init__(
        self,
        result: ReplySendRecoveryResult | None = None,
    ) -> None:
        self.commands: list[RecoverStaleReplySendsCommand] = []
        self.result = result or ReplySendRecoveryResult(
            recovered=0,
            sending_marked_unknown=0,
            reserved_cancelled=0,
        )

    async def recover_stale(
        self,
        command: RecoverStaleReplySendsCommand,
    ) -> ReplySendRecoveryResult:
        self.commands.append(command)
        return self.result


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        GATE_REPLY_SEND_RECOVERY_ENABLED=True,
        **overrides,
    )


def test_recovery_settings_are_disabled_and_bounded_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "GATE_REPLY_SEND_RECOVERY_ENABLED",
        "GATE_REPLY_SEND_RECOVERY_STALE_SECONDS",
        "GATE_REPLY_SEND_RECOVERY_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.gate_reply_send_recovery_enabled is False
    assert settings.gate_reply_send_recovery_stale_seconds == 900
    assert settings.gate_reply_send_recovery_limit == 100


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("GATE_REPLY_SEND_RECOVERY_STALE_SECONDS", 899),
        ("GATE_REPLY_SEND_RECOVERY_STALE_SECONDS", 86_401),
        ("GATE_REPLY_SEND_RECOVERY_LIMIT", 0),
        ("GATE_REPLY_SEND_RECOVERY_LIMIT", 101),
    ],
)
def test_recovery_settings_reject_unsafe_bounds(
    override: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        _settings(**{override: value})


def test_valid_recovery_command_is_delegated_once() -> None:
    recovered_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    recovery = RecordingRecovery()
    command = RecoverStaleReplySendsCommand(
        stale_before=recovered_at - timedelta(minutes=15),
        recovered_at=recovered_at,
        limit=100,
    )

    result = asyncio.run(RecoverStaleReplySendsService(recovery).execute(command))

    assert result.recovered == 0
    assert recovery.commands == [command]


@pytest.mark.parametrize(
    "command",
    [
        RecoverStaleReplySendsCommand(
            stale_before=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            recovered_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        ),
        RecoverStaleReplySendsCommand(
            stale_before=datetime(2026, 7, 14, 11, 45),
            recovered_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        ),
        RecoverStaleReplySendsCommand(
            stale_before=datetime(2026, 7, 14, 11, 45, tzinfo=UTC),
            recovered_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            limit=0,
        ),
        RecoverStaleReplySendsCommand(
            stale_before=datetime(2026, 7, 14, 11, 45, tzinfo=UTC),
            recovered_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            limit=101,
        ),
    ],
)
def test_invalid_recovery_command_fails_before_adapter(
    command: RecoverStaleReplySendsCommand,
) -> None:
    recovery = RecordingRecovery()

    with pytest.raises(ValueError):
        asyncio.run(RecoverStaleReplySendsService(recovery).execute(command))

    assert recovery.commands == []


def test_disabled_runtime_returns_before_database_access() -> None:
    result = asyncio.run(
        run_reply_send_recovery_once(
            settings=Settings(
                _env_file=None,
                GATE_REPLY_SEND_RECOVERY_ENABLED=False,
            )
        )
    )

    assert result.enabled is False
    assert result.disposition == "disabled"
    assert result.recovered == 0


def test_enabled_runtime_executes_exactly_one_bounded_command() -> None:
    recovered_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    recovery = RecordingRecovery(
        ReplySendRecoveryResult(
            recovered=3,
            sending_marked_unknown=2,
            reserved_cancelled=1,
        )
    )
    settings = _settings(
        GATE_REPLY_SEND_RECOVERY_STALE_SECONDS=1_200,
        GATE_REPLY_SEND_RECOVERY_LIMIT=7,
        TELEGRAM_BOT_TOKEN="",
    )

    result = asyncio.run(
        run_reply_send_recovery_once(
            settings=settings,
            recovery=recovery,
            now=recovered_at,
        )
    )

    assert settings.telegram_bot_token == ""
    assert result.enabled is True
    assert result.disposition == "recovered"
    assert result.recovered == 3
    assert result.sending_marked_unknown == 2
    assert result.reserved_cancelled == 1
    assert recovery.commands == [
        RecoverStaleReplySendsCommand(
            stale_before=recovered_at - timedelta(seconds=1_200),
            recovered_at=recovered_at,
            limit=7,
        )
    ]


def test_enabled_cli_runtime_requires_only_database_url() -> None:
    settings = _settings(DATABASE_URL="", TELEGRAM_BOT_TOKEN="")

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        asyncio.run(worker_module._run_from_settings(settings))


def test_runtime_logging_is_aggregate_and_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_database = "postgresql+asyncpg://private-user:private-password@private-host/gate"
    private_token = "private-telegram-token"
    recovery = RecordingRecovery(
        ReplySendRecoveryResult(
            recovered=4,
            sending_marked_unknown=3,
            reserved_cancelled=1,
        )
    )
    runtime_logger = logging.getLogger("gate.reply-send-recovery.test")

    with caplog.at_level(logging.INFO, logger=runtime_logger.name):
        asyncio.run(
            run_reply_send_recovery_once(
                settings=_settings(
                    DATABASE_URL=private_database,
                    TELEGRAM_BOT_TOKEN=private_token,
                ),
                recovery=recovery,
                now=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
                runtime_logger=runtime_logger,
            )
        )

    wire = caplog.text
    assert "enabled=True" in wire
    assert "disposition=recovered" in wire
    assert "recovered=4" in wire
    assert "sending_marked_unknown=3" in wire
    assert "reserved_cancelled=1" in wire
    assert private_database not in wire
    assert private_token not in wire
    assert "recipient" not in wire
    assert "business_connection" not in wire


def test_cli_failure_logging_does_not_expose_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_detail = "private-database-host-and-credential"

    async def fail_without_echoing_settings(_settings: Settings) -> object:
        raise RuntimeError(private_detail)

    monkeypatch.setattr(worker_module, "_run_from_settings", fail_without_echoing_settings)
    monkeypatch.setattr(worker_module, "get_settings", lambda: Settings(_env_file=None))

    with caplog.at_level(logging.ERROR, logger=worker_module.logger.name):
        with pytest.raises(SystemExit) as exc_info:
            worker_module.main()

    assert exc_info.value.code == 1
    assert "Reply-send recovery failed" in caplog.text
    assert private_detail not in caplog.text


def test_recovery_modules_have_no_telegram_dependency() -> None:
    for module in (application_module, infrastructure_module, worker_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        assert not any(
            imported.startswith("app.services.telegram") for imported in imported_modules
        )
        assert "telegram_bot_token" not in source


def test_runtime_has_no_loop_or_scheduler_and_exposes_one_shot_script() -> None:
    source_path = Path(worker_module.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(isinstance(node, (ast.For, ast.AsyncFor, ast.While)) for node in ast.walk(tree))

    pyproject = tomllib.loads(
        (source_path.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["scripts"]["gate-reply-send-recovery"] == (
        "app.worker.reply_send_recovery:main"
    )
