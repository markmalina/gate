"""Synthetic bootstrap contract and real shared-transport admission checks."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.config import Settings
from app.infrastructure.baseline_observation import (
    KEYS,
    marker_query,
    parse_marker,
    process_metadata,
    validate_process_rows,
)
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.services.process_send_fence import (
    BaselineSendClosed,
    ProcessSendFence,
    install_process_send_fence,
)
from app.services.telegram import TelegramClient
from app.worker.reply_sequences import run_reply_sequence_once


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def fields() -> dict[str, str]:
    return {
        "boot": "a" * 32,
        "source": "b" * 40,
        **{key: "c" * 64 for key in ("replica", "deployment", "workspace", "config", "identity")},
        "schema": "0111_reply_documents",
        "send": "0",
        "worker": "0",
        "inflight": "0",
    }


def observation() -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    values = fields()
    now = datetime.now(UTC)
    return (
        [
            {
                "application_name": "gate-g1a:" + values["boot"],
                "query": marker_query(values),
                "age": 1.0,
                "backend_start": now - timedelta(seconds=10),
                "query_start": now - timedelta(seconds=1),
                "xact_start": None,
                "state": "idle",
                "pid": 1,
            }
        ],
        [{key: values[key] for key in ("source", "replica", "deployment", "workspace", "config")}],
    )


def test_whole_marker_fits_default_tracking_and_roundtrips() -> None:
    query = marker_query(fields())
    assert query.isascii() and len(query.encode()) < 1024
    assert len("gate-g1a:" + fields()["boot"]) == 41
    assert parse_marker(query) == fields()
    assert tuple(parse_marker(query)) == KEYS
    rows, expected = observation()
    assert validate_process_rows(rows, expected=expected, identity="c" * 64) == [fields()]


@pytest.mark.parametrize(
    "change",
    [
        lambda q: q[:-1],
        lambda q: q + " ",
        lambda q: q.replace(";send=0", ";send=2"),
        lambda q: q.replace(";inflight=0", ";inflight=-1"),
        lambda q: q.replace(";inflight=0", ";inflight=01"),
        lambda q: q.replace(";send=0", ";send=0;send=0"),
        lambda q: q.replace(";send=0;worker=0", ";worker=0;send=0"),
        lambda q: q.replace("0111_reply_documents", "0112_reply_sequence_state"),
        lambda q: q.replace("SELECT 1", "SELECT pg_sleep(5)"),
        lambda q: q.replace(";send=0", ";send=0 */ SELECT 1 /*"),
    ],
)
def test_strict_marker_rejects_mutation(change: object) -> None:
    with pytest.raises(ValueError):
        parse_marker(change(marker_query(fields())))  # type: ignore[operator]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("age", 15.01),
        ("age", -0.01),
        ("age", None),
        ("query", None),
        ("application_name", "gate-g1a:forged"),
        ("state", "active"),
        ("xact_start", datetime.now(UTC)),
        ("backend_start", None),
        ("pid", None),
    ],
)
def test_missing_stale_hidden_or_unbound_process_never_accepted(key: str, value: object) -> None:
    rows, expected = observation()
    rows[0][key] = value
    with pytest.raises(ValueError):
        validate_process_rows(rows, expected=expected, identity="c" * 64)


def test_missing_duplicate_wrong_identity_and_nonfrozen_fleet_rejected() -> None:
    rows, expected = observation()
    for observed, pinned, identity in [
        ([], expected, "c" * 64),
        (rows * 2, expected * 2, "c" * 64),
        (rows, expected, "d" * 64),
        (rows, [{**expected[0], "source": "d" * 40}], "c" * 64),
    ]:
        with pytest.raises(ValueError):
            validate_process_rows(observed, expected=pinned, identity=identity)
    for key in ("send", "worker", "inflight"):
        rows[0]["query"] = marker_query({**fields(), key: "1"})
        with pytest.raises(ValueError, match="process_not_frozen"):
            validate_process_rows(rows, expected=expected, identity="c" * 64)


def test_config_digest_has_explicit_nonsecret_inputs() -> None:
    settings = Settings(RAILWAY_GIT_COMMIT_SHA="b" * 40, GATE_WORKSPACE_ID="synthetic")
    environ = {"RAILWAY_REPLICA_ID": "replica", "RAILWAY_DEPLOYMENT_ID": "deployment"}
    first = process_metadata(settings, boot="a" * 32, environ=environ)
    changed = settings.model_copy(update={"telegram_bot_token": "different-secret"})
    assert process_metadata(changed, boot="a" * 32, environ=environ) == first
    with pytest.raises(ValueError, match="provider_binding_unknown"):
        process_metadata(settings, boot="a" * 32, environ={})


def test_config_interoperability_fixture() -> None:
    from app.infrastructure.baseline_observation import config_evidence, digest

    fixture = json.loads(Path("contracts/baseline-observation/fixture.json").read_text())
    inputs = dict(fixture["settings_input"])
    inputs["gate_reply_sequence_poll_seconds"] = float(inputs["gate_reply_sequence_poll_seconds"])
    settings = Settings().model_copy(update=inputs)
    assert config_evidence(settings) == fixture["config_evidence"]
    assert digest(config_evidence(settings)) == fixture["config_sha256"]


@pytest.mark.anyio
async def test_shared_fence_blocks_plain_business_multipart_and_unknown_write() -> None:
    fence = ProcessSendFence(Settings(GATE_SEND_ENABLED=False))
    previous = install_process_send_fence(fence)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {}})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            for client in (
                TelegramClient("one", http_client=http),
                TelegramClient("two", http_client=http),
            ):
                attempts = [
                    client.send_plain_message(chat_id="synthetic", text="test"),
                    client.send_message(chat_id="synthetic", text="test"),
                    client._post_telegram_multipart(
                        url="https://api.telegram.org/botone/sendDocument",
                        payload={},
                        file=("document", ("test.txt", b"test", "text/plain")),
                    ),
                    client._post_telegram_json(
                        url="https://api.telegram.org/botone/newMethod", payload={}
                    ),
                ]
                for attempt in attempts:
                    with pytest.raises(BaselineSendClosed):
                        await attempt
            assert calls == [] and fence.inflight == 0
    finally:
        install_process_send_fence(previous)


@pytest.mark.anyio
async def test_inflight_counter_covers_http_and_cancellation_and_requires_fresh_observation() -> (
    None
):
    entered, release = asyncio.Event(), asyncio.Event()
    fence = ProcessSendFence(Settings(GATE_SEND_ENABLED=True), observations_required=True)
    previous = install_process_send_fence(fence)

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"ok": True, "result": {}})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = TelegramClient("synthetic", http_client=http)
            with pytest.raises(BaselineSendClosed):
                await client.send_plain_message(chat_id="synthetic", text="test")
            fence.last_observed = monotonic() - 16
            with pytest.raises(BaselineSendClosed):
                await client.send_plain_message(chat_id="synthetic", text="test")
            fence.last_observed = monotonic()
            task = asyncio.create_task(client.send_plain_message(chat_id="synthetic", text="test"))
            await entered.wait()
            assert fence.inflight == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert fence.inflight == 0
    finally:
        install_process_send_fence(previous)


@pytest.mark.anyio
async def test_frozen_queue_and_worker_do_not_touch_database() -> None:
    from app.application.reply_sequences import ReplySequenceError

    fence = ProcessSendFence(Settings(GATE_SEND_ENABLED=False))
    previous = install_process_send_fence(fence)
    session = AsyncMock()
    try:
        with pytest.raises(ReplySequenceError, match="admission is closed"):
            await SqlReplySequenceWriter(session).queue(None)  # type: ignore[arg-type]
        assert (
            await run_reply_sequence_once(
                settings=fence.settings,
                session_factory=session,
                telegram=None,  # type: ignore[arg-type]
            )
            == 0
        )
        assert not session.mock_calls
    finally:
        install_process_send_fence(previous)


@pytest.mark.anyio
async def test_standalone_worker_uses_its_engine_and_closes_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure import baseline_observation as module
    from app.services.process_send_fence import process_send_fence
    from app.worker import telegram_processing as worker

    engine = AsyncMock()
    observed: list[object] = []
    completed = asyncio.Event()

    async def observer(*, engine: object, fence: ProcessSendFence) -> None:
        observed.append(engine)
        fence.last_observed = monotonic()
        try:
            await asyncio.Event().wait()
        finally:
            fence.last_observed = None
            completed.set()

    async def run(settings: Settings, actual_engine: object) -> str:
        assert actual_engine is engine
        assert process_send_fence().observer_owned
        assert process_send_fence().last_observed is not None
        return "observed"

    monkeypatch.setattr(module, "run_baseline_observer", observer)
    monkeypatch.setattr(worker, "create_async_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(worker, "_run_worker_with_engine", run)
    previous = install_process_send_fence(ProcessSendFence(Settings()))
    try:
        result = await worker._run_from_settings(
            Settings(
                DATABASE_URL="postgresql+asyncpg://synthetic",
                GATE_TELEGRAM_WORKER_ENABLED=True,
            )
        )
        assert result == "observed"
        assert observed == [engine] and completed.is_set()
        engine.dispose.assert_awaited_once()
    finally:
        install_process_send_fence(previous)


@pytest.mark.anyio
async def test_asgi_worker_reuses_observer_and_rejects_different_engine_config() -> None:
    from app.infrastructure.baseline_observation import standalone_process_observation

    settings = Settings(DATABASE_URL="postgresql+asyncpg://synthetic")
    fence = ProcessSendFence(
        settings,
        observations_required=True,
        observer_owned=True,
        last_observed=monotonic(),
    )
    previous = install_process_send_fence(fence)
    engine = AsyncMock()
    try:
        async with standalone_process_observation(engine=engine, settings=settings):
            pass
        assert not engine.mock_calls
        changed = settings.model_copy(update={"database_url": "postgresql+asyncpg://different"})
        with pytest.raises(BaselineSendClosed, match="configuration_mismatch"):
            async with standalone_process_observation(engine=engine, settings=changed):
                pytest.fail("different loaded DB must not be attested by the first engine")
        fence.last_observed = None
        with pytest.raises(BaselineSendClosed, match="observation_unavailable"):
            async with standalone_process_observation(engine=engine, settings=settings):
                pytest.fail("stale observer must not create a second process boot")
    finally:
        install_process_send_fence(previous)
