import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from app.infrastructure.watch_voice_metrics import OBSERVATION
from app.worker import watch_voice as worker
from app.worker.dropbox_calls import DropboxCallWorkerError
from tests.test_watch_voice import World, database, exact_settings, notes


@pytest.mark.parametrize(
    "failure", ["dropbox_call_oauth_transport_failed", "dropbox_call_download_transport_failed"]
)
def test_dropbox_outage_does_not_hold_archived_note_or_repeat_effect(
    tmp_path,
    monkeypatch,
    failure,
):
    world = World()
    world.patches(monkeypatch)

    async def scenario():
        settings = exact_settings()
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.ensure_cursor(settings, factory)
                await worker.store_entry(settings, factory, http, "synthetic", world.entry)
                monkeypatch.setattr(
                    worker,
                    "scan_voice_source",
                    AsyncMock(side_effect=DropboxCallWorkerError(failure)),
                )
                assert await worker.process_watch_voice(settings, factory, http)
                assert (await notes(factory))[0].delivery_state == "delivered"
                assert OBSERVATION.scan_error_code == failure
                assert not await worker.process_watch_voice(settings, factory, http)
                assert world.provider_calls == 1 and world.posts == 1

    asyncio.run(scenario())


def test_dropbox_identity_mismatch_remains_fail_closed(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)
    monkeypatch.setattr(
        worker,
        "_verify_account_id",
        AsyncMock(side_effect=DropboxCallWorkerError("dropbox_call_identity_mismatch")),
    )

    async def scenario():
        async with database(tmp_path) as factory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                with pytest.raises(DropboxCallWorkerError, match="identity_mismatch"):
                    await worker.process_watch_voice(exact_settings(), factory, http)
                assert world.provider_calls == 0 and world.posts == 0

    asyncio.run(scenario())


def test_backlog_continues_without_poll_sleep_and_idle_returns_to_poll(monkeypatch):
    results = iter((True, True, False))
    delays = []
    original_client = httpx.AsyncClient

    def forbidden_request(request):
        raise AssertionError("loop fixture made an external request")

    def client(**kwargs):
        return original_client(
            transport=httpx.MockTransport(forbidden_request), trust_env=False, **kwargs
        )

    async def process(*args):
        return next(results)

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(worker, "process_watch_voice", process)
    monkeypatch.setattr(worker.httpx, "AsyncClient", client)
    monkeypatch.setattr(worker.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            worker.run_watch_voice_worker_loop(
                settings=exact_settings(), session_factory=AsyncMock()
            )
        )
    assert delays == [0, 0, 30]


@pytest.mark.parametrize("failure", [RuntimeError("synthetic private exception"), TimeoutError()])
def test_voice_monitor_failure_does_not_stop_existing_calls_loop(monkeypatch, failure):
    from app.worker import dropbox_calls as calls

    process = AsyncMock()
    monitor = AsyncMock(side_effect=failure)
    sleeps = []
    original_client = httpx.AsyncClient

    async def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(calls, "_validate_settings", lambda cfg: None)
    monkeypatch.setattr(calls, "process_dropbox_call_changes", process)
    monkeypatch.setattr("app.infrastructure.watch_voice_monitor.observe_watch_voice", monitor)
    monkeypatch.setattr(calls.asyncio, "sleep", sleep)
    monkeypatch.setattr(
        calls.httpx,
        "AsyncClient",
        lambda **kw: original_client(
            transport=httpx.MockTransport(lambda request: pytest.fail("external I/O")),
            trust_env=False,
            **kw,
        ),
    )
    cfg = exact_settings().model_copy(update={"gate_dropbox_call_intake_enabled": True})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(calls.run_dropbox_call_worker_loop(settings=cfg, session_factory=AsyncMock()))
    assert process.await_count == monitor.await_count == 2
