"""No provider/database effects: protected-input and durable transport contract."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from app.infrastructure.reply_transport_budget import SqlTelegramTransportBudget
from app.models.reply_sequences import ReplyCanaryScope
from app.services.telegram import TelegramApiError, TelegramClient
from app.services.telegram_delivery_gate import (
    CanaryDelivery,
    canary_delivery,
    register_gate,
    unregister_gate,
)
from app.worker.reply_sequence_control import parse_canary_arm


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("raw", ["{}", "[]", "x" * 65537, '{"grants":[{}]}'])
def test_protected_scope_is_bounded_and_rejects_missing_fields(raw):
    with pytest.raises(ValueError):
        parse_canary_arm(raw)


@pytest.mark.anyio
async def test_bound_notification_still_requires_canary_reservation_when_flag_is_off(monkeypatch):
    import app.core.config as config

    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(gate_reply_sequence_canary_enabled=False)
    )
    calls = []

    class Gate:
        async def before(self, payload):
            import time

            return time.monotonic()

        async def reserve_canary(self, intent, payload):
            assert intent.execution_id == "closed-execution"
            raise TelegramApiError(error_code=403, description="Canary admission closed")

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    gate = Gate()
    register_gate("123", gate)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            telegram = TelegramClient("123:synthetic", http_client=client)
            with (
                canary_delivery(
                    CanaryDelivery(
                        kind="notification",
                        principal_subject="owner",
                        account_id=None,
                        dialog_ref=None,
                        body_sha256=None,
                        destination="999",
                        execution_id="closed-execution",
                    )
                ),
                pytest.raises(TelegramApiError),
            ):
                await telegram.send_plain_message(chat_id="999", text="synthetic")
        assert not calls
    finally:
        unregister_gate("123", gate)


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["sendMessage", "sendDocument", "sendPhoto"])
@pytest.mark.parametrize("state", ["active", "closed", "absent"])
async def test_durable_scope_fences_all_sends_after_flag_off(method, state, monkeypatch):
    import app.core.config as config

    now = datetime.now(UTC)
    scope = (
        None
        if state == "absent"
        else ReplyCanaryScope(
            workspace_ref="synthetic",
            execution_id="exact",
            state=state,
            expires_at=now + timedelta(minutes=5),
            max_attempts=6,
            attempts_reserved=0,
        )
    )
    reads, calls = [], []
    budget = SimpleNamespace(
        blocked_indefinitely=False, blocked_until=None, next_allowed_at=now - timedelta(seconds=1)
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, model, identity, **kwargs):
            reads.append(model)
            return scope

        async def scalar(self, query):
            return budget

        async def execute(self, query):
            pass

        async def commit(self):
            pass

    async def db_now(session):
        return now

    settings = SimpleNamespace(
        gate_reply_sequence_canary_enabled=False,
        gate_reply_sequence_stop_on_inbound_enabled=True,
        telegram_bot_token="123:synthetic",
    )
    runtime = SimpleNamespace(
        settings=settings,
        sessions=Session,
        db_now=db_now,
        workspace="synthetic",
        bot_resource="synthetic",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    gate = SqlTelegramTransportBudget(runtime)
    register_gate("123", gate)

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            telegram = TelegramClient("123:synthetic", http_client=client)

            async def send():
                url = f"https://synthetic.invalid/bot123:synthetic/{method}"
                if method == "sendDocument":
                    return await telegram._post_telegram_multipart(
                        url=url,
                        payload={"chat_id": "outside"},
                        file=("document", ("x.txt", b"synthetic", "text/plain")),
                    )
                return await telegram._post_telegram_json(
                    url=url, payload={"chat_id": "outside", "text": "synthetic"}
                )

            if state == "active":
                with pytest.raises(TelegramApiError):
                    await send()
                assert not calls
            else:
                await send()
                assert len(calls) == 1
            assert ReplyCanaryScope in reads
    finally:
        unregister_gate("123", gate)


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["sendMessage", "sendDocument", "sendPhoto"])
@pytest.mark.parametrize("state", ["active", "closed", "absent", "legacy", "db_error"])
async def test_actual_startup_installs_fence_with_all_flags_off(method, state, monkeypatch):
    import app.core.config as config
    import app.infrastructure.database as database
    from app.services.telegram_delivery_gate import registered_gate

    calls, reads, disposed = [], [], []
    scope = SimpleNamespace(state=state) if state in {"active", "closed"} else None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def scalar(self, query):
            reads.append("schema")
            if state == "db_error":
                raise RuntimeError("synthetic DB unavailable")
            return None if state == "legacy" else "reply_canary_scopes"

        async def get(self, model, identity):
            assert model is ReplyCanaryScope
            assert identity == "synthetic"
            reads.append("scope")
            return scope

    class Engine:
        async def dispose(self):
            disposed.append(True)

    settings = SimpleNamespace(
        gate_reply_sequence_canary_enabled=False,
        gate_reply_sequence_stop_on_inbound_enabled=False,
        gate_reply_sequence_dispatch_v2_enabled=False,
        gate_reply_sequence_worker_enabled=False,
        gate_reply_sequence_parallel_enabled=False,
        telegram_bot_token="124:synthetic",
        gate_workspace_id="synthetic",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "create_async_engine", lambda *a, **kw: Engine())
    monkeypatch.setattr(database, "async_sessionmaker", lambda *a, **kw: Session)

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    assert registered_gate("124") is None
    async with database.database_runtime_lifespan("synthetic://offline") as runtime:
        assert runtime.sequences is None
        assert runtime.startup_fence is not None
        assert registered_gate("124") is runtime.startup_fence[1]
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            telegram = TelegramClient("124:synthetic", http_client=client)

            async def send():
                url = f"https://synthetic.invalid/bot124:synthetic/{method}"
                if method == "sendDocument":
                    return await telegram._post_telegram_multipart(
                        url=url,
                        payload={"chat_id": "outside"},
                        file=("document", ("x.txt", b"synthetic", "text/plain")),
                    )
                return await telegram._post_telegram_json(
                    url=url, payload={"chat_id": "outside", "text": "synthetic"}
                )

            if state in {"active", "db_error"}:
                with pytest.raises(TelegramApiError if state == "active" else RuntimeError):
                    await send()
                assert not calls
            else:
                await send()
                assert len(calls) == 1
    assert reads
    assert registered_gate("124") is None
    assert disposed == [True]


@pytest.mark.parametrize("name", ["alpha", "beta", "become", "aqub"])
@pytest.mark.parametrize("aliased", [True, False])
def test_actual_startup_rejects_other_product_alias_of_personal_bot(name, aliased, monkeypatch):
    import app.core.config as config
    import app.infrastructure.database as database
    from pydantic import SecretStr

    settings = SimpleNamespace(
        telegram_bot_token="124:primary",
        **{f"telegram_{name}_bot_token": SecretStr("124:other" if aliased else "125:other")},
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    def engine(*args, **kwargs):
        raise RuntimeError("synthetic engine boundary")

    monkeypatch.setattr(database, "create_async_engine", engine)
    with pytest.raises(
        ValueError if aliased else RuntimeError,
        match="identity must be isolated" if aliased else "synthetic engine boundary",
    ):
        database.build_database_runtime("synthetic://offline")


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["active", "closed", "absent", "legacy", "db_error"])
async def test_standalone_worker_installs_and_releases_same_durable_fence(state, monkeypatch):
    import asyncio
    from time import monotonic

    import app.infrastructure.database as database
    import app.infrastructure.process_admission as admission
    from app.core.config import Settings
    from app.services.process_send_fence import BaselineSendClosed
    from app.services.telegram_delivery_gate import registered_gate
    from app.worker import telegram_processing as worker

    calls, reads, disposed = [], [], []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, query):
            reads.append(True)
            if state == "db_error":
                raise RuntimeError("synthetic DB unavailable")
            revision = "0111_reply_documents" if state == "legacy" else "0115_reply_canary_binding"
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [revision]))

        async def scalar(self, query):
            reads.append(True)
            if state == "db_error":
                raise RuntimeError("synthetic DB unavailable")
            return None if state == "legacy" else "reply_canary_scopes"

        async def get(self, model, identity):
            return SimpleNamespace(state=state) if state in {"active", "closed"} else None

    class Engine:
        async def dispose(self):
            disposed.append(True)

    settings = Settings(
        _env_file=None,
        DATABASE_URL="synthetic://offline",
        TELEGRAM_BOT_TOKEN="126:synthetic",
        GATE_TELEGRAM_WORKER_ENABLED=True,
    )
    monkeypatch.setattr(database, "create_async_engine", lambda *a, **kw: Engine())
    monkeypatch.setattr(database, "async_sessionmaker", lambda *a, **kw: Session)

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def once(**kwargs):
        assert registered_gate("126") is not None
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            await TelegramClient("126:synthetic", http_client=client).send_plain_message(
                chat_id="outside", text="synthetic"
            )
        return "synthetic-done"

    async def drain(_sessions):
        pass

    async def observer(*, engine, fence):
        fence.last_observed = monotonic()
        await asyncio.Future()

    monkeypatch.setattr(admission, "run_baseline_observer", observer)
    monkeypatch.setattr(worker, "run_telegram_worker_once", once)
    monkeypatch.setattr(worker, "_drain_beta_context", drain)
    if state != "legacy":
        # Candidate schema with every v2 flag off has no verified G2 callback,
        # irrespective of whether the durable canary is active/closed/absent.
        with pytest.raises(BaselineSendClosed):
            await worker._run_from_settings(settings)
        assert not calls
    else:
        assert await worker._run_from_settings(settings) == "synthetic-done"
        assert len(calls) == 1
    assert reads and disposed == [True]
    assert registered_gate("126") is None


@pytest.mark.anyio
@pytest.mark.parametrize("entrypoint", ["worker", "pilot"])
async def test_standalone_entrypoints_reject_primary_bot_alias_before_engine(
    entrypoint, monkeypatch
):
    import app.infrastructure.database as database
    from app.core.config import Settings
    from app.worker import become_pilot_maintenance as pilot
    from app.worker import telegram_processing as worker

    settings = Settings(
        _env_file=None,
        DATABASE_URL="synthetic://offline",
        TELEGRAM_BOT_TOKEN="126:synthetic",
        TELEGRAM_BECOME_BOT_TOKEN="126:other",
        GATE_TELEGRAM_WORKER_ENABLED=True,
    )

    def engine(*args, **kwargs):
        pytest.fail("alias must reject before engine creation")

    monkeypatch.setattr(database, "create_async_engine", engine)
    monkeypatch.setattr(pilot, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="identity must be isolated"):
        if entrypoint == "worker":
            await worker._run_from_settings(settings)
        else:
            await pilot._run(SimpleNamespace(command="status"))
