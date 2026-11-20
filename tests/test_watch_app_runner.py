import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.worker import watch_app_runner as runner


def test_default_off(monkeypatch):
    monkeypatch.delenv("GATE_WATCH_APP_RUNNER_ENABLED", raising=False)
    monkeypatch.setattr(runner, "get_settings", MagicMock(side_effect=AssertionError))
    assert asyncio.run(runner.main()) == 0


@pytest.mark.parametrize("discovery_outage", [False, True])
def test_active_writer_drains_even_on_dropbox_outage(monkeypatch, discovery_outage):
    monkeypatch.setenv("GATE_WATCH_APP_RUNNER_ENABLED", "true")
    monkeypatch.setenv("GATE_WATCH_APP_PRIVATE_KEY", "synthetic")
    monkeypatch.setenv("GATE_WATCH_APP_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("GATE_WATCH_APP_ID", "123")
    monkeypatch.setenv("GATE_WATCH_APP_INSTALLATION_ID", "456")
    monkeypatch.setenv("GATE_WATCH_APP_SLUG", "watch-fixture")
    engine = SimpleNamespace(dispose=AsyncMock())
    factory = MagicMock()
    factory.return_value.__aenter__.return_value.get = AsyncMock(
        return_value=SimpleNamespace(writer_mode="app")
    )
    monkeypatch.setattr(runner, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(runner, "async_sessionmaker", MagicMock(return_value=factory))
    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(
            gate_workspace_id="mark-personal",
            gate_watch_voice_enabled=False,
            dropbox_call_account_id="test",
        ),
    )
    discover = AsyncMock(
        side_effect=runner.WatchVoiceError("dropbox_unavailable") if discovery_outage else None
    )
    monkeypatch.setattr(runner, "discover_early_cards", discover)
    monkeypatch.setattr(runner, "issue_mark_token", AsyncMock(return_value="temporary"))
    client = SimpleNamespace(preflight=AsyncMock())
    monkeypatch.setattr(runner, "AppInboxClient", MagicMock(return_value=client))
    drain = AsyncMock(return_value={"result": "processed"})
    monkeypatch.setattr(runner, "drain_saved", drain)

    assert asyncio.run(runner.main()) == int(discovery_outage)
    assert discover.await_args.kwargs == {"writer": "app"}
    assert drain.await_args.kwargs == {"writer": "app"}
    engine.dispose.assert_awaited_once()

    factory.return_value.__aenter__.return_value.get.return_value.writer_mode = "legacy_actions"
    discover.reset_mock()
    runner.issue_mark_token.reset_mock()
    assert asyncio.run(runner.main()) == 1
    discover.assert_not_awaited()
    runner.issue_mark_token.assert_not_awaited()
