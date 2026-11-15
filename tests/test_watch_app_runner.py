import asyncio
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


def load_runner():
    names = ["sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio", "app.core.config",
             "app.infrastructure.watch_app_auth", "app.infrastructure.watch_app_inbox",
             "app.infrastructure.watch_voice_early_cards"]
    fake = {name: types.ModuleType(name) for name in names}
    fake["sqlalchemy.ext.asyncio"].async_sessionmaker = lambda *_a, **_kw: None
    fake["sqlalchemy.ext.asyncio"].create_async_engine = lambda *_a, **_kw: None
    fake["app.core.config"].get_settings = lambda: types.SimpleNamespace(
        gate_workspace_id="mark-personal", gate_watch_voice_enabled=False,
        dropbox_call_account_id="")
    fake["app.infrastructure.watch_app_auth"].issue_mark_token = None
    fake["app.infrastructure.watch_app_inbox"].AppInboxClient = None
    fake["app.infrastructure.watch_voice_early_cards"].run_early_cards = None
    with patch.dict(sys.modules, fake):
        spec = importlib.util.spec_from_file_location("candidate_runner", "app/worker/watch_app_runner.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class RunnerTests(unittest.TestCase):
    def test_default_off(self):
        module = load_runner()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(asyncio.run(module.main()), 0)

    def test_missing_credentials_fail_closed(self):
        module = load_runner()
        with patch.dict(os.environ, {"GATE_WATCH_APP_RUNNER_ENABLED": "true"}, clear=True):
            self.assertEqual(asyncio.run(module.main()), 1)

    def test_one_enabled_pass(self):
        module = load_runner()
        engine = MagicMock()
        engine.dispose = AsyncMock()
        client = MagicMock()
        client.preflight = AsyncMock()
        module.create_async_engine = MagicMock(return_value=engine)
        module.async_sessionmaker = MagicMock(return_value="factory")
        module.get_settings = lambda: types.SimpleNamespace(
            gate_workspace_id="mark-personal", gate_watch_voice_enabled=False,
            dropbox_call_account_id="test")
        module.issue_mark_token = AsyncMock(return_value="temporary")
        module.AppInboxClient = MagicMock(return_value=client)
        module.run_early_cards = AsyncMock(return_value={"result": "processed"})
        values = {"GATE_WATCH_APP_RUNNER_ENABLED": "true", "GATE_WATCH_APP_PRIVATE_KEY": "fake",
                  "GATE_WATCH_APP_DATABASE_URL": "postgresql://test:test@localhost/test"}
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(asyncio.run(module.main()), 0)
        module.run_early_cards.assert_awaited_once()
        engine.dispose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
