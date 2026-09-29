"""Actual startup schema handoff and the shared primary-bot HTTP boundary."""

import asyncio
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core.config import Settings
from app.infrastructure import process_admission as admission
from app.infrastructure.database import DatabaseRuntime
from app.infrastructure.reply_sequence_epoch import ContinuityDenied
from app.services.process_send_fence import (
    BaselineSendClosed,
    installed_process_send_fence,
)
from app.services.telegram import TelegramClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings(**changes):
    return Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="127:primary",
        TELEGRAM_ALPHA_BOT_TOKEN="128:other-product",
        **changes,
    )


def database(schema, *, candidate=True):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, query):
            if schema == "DB_ERROR":
                raise RuntimeError("synthetic private DSN must not escape")
            values = schema if isinstance(schema, list) else [schema]
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: values))

    return DatabaseRuntime(
        engine=AsyncMock(),
        session_factory=Session,
        sequences=SimpleNamespace() if candidate else None,
    )


async def send(client, method):
    if method == "sendDocument":
        return await client._post_telegram_multipart(
            url=f"https://synthetic.invalid/bot{client._credential}/sendDocument",
            payload={"chat_id": "synthetic"},
            file=("document", ("synthetic.txt", b"test", "text/plain")),
        )
    return await client._post_telegram_json(
        url=f"https://synthetic.invalid/bot{client._credential}/{method}",
        payload={"chat_id": "synthetic", "text": "synthetic"},
    )


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["sendMessage", "sendDocument", "editMessageText"])
@pytest.mark.parametrize("health", ["valid", "missing_epoch", "lost_health"])
async def test_candidate_common_boundaries_require_actual_primary_health(
    method, health, monkeypatch
):
    from app.infrastructure import reply_sequence_process_health as g2

    checks, calls = [], []

    async def check(runtime, *, bot_resource_id):
        checks.append(bot_resource_id)
        if health != "valid":
            raise ContinuityDenied("synthetic_health_denied")

    monkeypatch.setattr(g2, "require_verified_process_health", check)
    runtime = database(admission.CANDIDATE_SCHEMA)
    async with admission.runtime_process_admission(runtime, settings=settings()):
        # Separate client objects using the same identity get the same guard.
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    calls.append(request)
                    or httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
                )
            )
        ) as http:
            client = TelegramClient("127:alternate-instance", http_client=http)
            if health == "valid":
                await send(client, method)
                assert len(calls) == 1
            else:
                with pytest.raises(BaselineSendClosed):
                    await send(client, method)
                assert not calls
    from app.application.reply_sequence_policy import text_digest

    assert checks == [text_digest("telegram-bot:127")]


@pytest.mark.anyio
@pytest.mark.parametrize("identity,allowed", [("128", True), ("129", False), ("invalid", False)])
async def test_distinct_configured_product_keeps_own_contract_but_unknown_identity_denies(
    identity, allowed
):
    calls = []
    async with admission.runtime_process_admission(
        database(admission.CANDIDATE_SCHEMA, candidate=False), settings=settings()
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(200, json={"ok": True})
            )
        ) as http:
            client = TelegramClient(identity + ":synthetic", http_client=http)
            if allowed:
                await send(client, "sendMessage")
            else:
                with pytest.raises(BaselineSendClosed):
                    await send(client, "sendMessage")
    assert len(calls) == int(allowed)


@pytest.mark.anyio
@pytest.mark.parametrize("identity", ["127", "128"])
async def test_global_freeze_closes_primary_and_distinct_product_before_health_or_http(
    identity, monkeypatch
):
    from app.infrastructure import reply_sequence_process_health as g2

    check = AsyncMock()
    monkeypatch.setattr(g2, "require_verified_process_health", check)
    async with admission.runtime_process_admission(
        database(admission.CANDIDATE_SCHEMA), settings=settings(GATE_SEND_ENABLED=False)
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: pytest.fail("frozen transport called HTTP")
            )
        ) as http:
            with pytest.raises(BaselineSendClosed):
                await send(TelegramClient(identity + ":synthetic", http_client=http), "sendMessage")
    check.assert_not_awaited()


@pytest.mark.anyio
async def test_candidate_all_flags_off_has_no_primary_send_window():
    async with admission.runtime_process_admission(
        database(admission.CANDIDATE_SCHEMA, candidate=False), settings=settings()
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: pytest.fail("unarmed transport called HTTP")
            )
        ) as http:
            with pytest.raises(BaselineSendClosed):
                await send(TelegramClient("127:primary", http_client=http), "sendMessage")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "schema",
    [
        [],
        ["0111_reply_documents", "0115_reply_canary_binding"],
        "0114_reply_continuity_epoch",
        "DB_ERROR",
    ],
)
async def test_wrong_or_unavailable_ledger_never_enters_runtime(schema):
    previous = installed_process_send_fence()
    with pytest.raises(BaselineSendClosed, match="process_schema_unavailable"):
        async with admission.runtime_process_admission(database(schema), settings=settings()):
            pytest.fail("unsupported schema yielded admission")
    assert installed_process_send_fence() is previous


@pytest.mark.anyio
async def test_schema_handoff_and_nested_borrow_preserve_one_observer(
    monkeypatch,
):
    observed, stopped = [], []

    async def observer(*, engine, fence):
        observed.append(engine)
        fence.last_observed = monotonic()
        try:
            await asyncio.Future()
        finally:
            stopped.append(True)

    monkeypatch.setattr(admission, "run_baseline_observer", observer)
    baseline = database("0111_reply_documents", candidate=False)
    configured = settings()
    async with admission.runtime_process_admission(
        baseline, settings=configured, wait_baseline=True
    ):
        fence = installed_process_send_fence()
        async with admission.runtime_process_admission(baseline, settings=configured):
            assert installed_process_send_fence() is fence
        assert installed_process_send_fence() is fence
        with pytest.raises(BaselineSendClosed, match="owner_mismatch"):
            async with admission.runtime_process_admission(
                database("0111_reply_documents"), settings=configured
            ):
                pytest.fail("second owner admitted")
    assert observed == [baseline.engine] and stopped == [True]
    async with admission.runtime_process_admission(
        database(admission.CANDIDATE_SCHEMA), settings=configured
    ):
        assert installed_process_send_fence().candidate_mode
    assert observed == [baseline.engine]
