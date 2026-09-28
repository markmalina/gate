"""Synthetic PostgreSQL concurrency checks for the irreversible canary cap."""

import asyncio
import hashlib
import importlib
import pkgutil
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import app.models
import httpx
import pytest
from app.infrastructure.reply_sequence_canary import (
    CanaryAttemptRequest,
    CanaryDenied,
    reserve_canary_attempt,
)
from app.models.reply_sequences import ReplyCanaryAttempt, ReplyCanaryScope
from app.services.telegram import TelegramApiError, TelegramClient
from app.services.telegram_delivery_gate import (
    CanaryDelivery,
    canary_delivery,
    register_gate,
    unregister_gate,
)
from sqlalchemy import func, select

from test_reply_sends_postgres import _postgres_sessions


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_parallel_attempts_share_one_durable_cap_across_sessions():
    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module("app.models." + module.name)
    async with _postgres_sessions() as sessions:
        account = uuid4()
        body = "a" * 64
        request = CanaryAttemptRequest(
            workspace_ref="synthetic",
            execution_id="exact-execution",
            attempt_key=uuid4(),
            kind="sequence",
            principal_subject="owner",
            account_id=account,
            dialog_ref="synthetic-dialog",
            body_sha256=body,
            notification_destination=None,
            notification_template=None,
        )
        async with sessions() as session:
            session.add(
                ReplyCanaryScope(
                    workspace_ref="synthetic",
                    execution_id="exact-execution",
                    state="active",
                    principal_subject="owner",
                    sequence_grants=[f"{account}:synthetic-dialog:{body}"],
                    notification_destination="owner-only",
                    notification_template="stop-v1",
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                    max_attempts=6,
                    attempts_reserved=0,
                    scope_sha256="b" * 64,
                )
            )
            await session.commit()

        async def attempt(index):
            async with sessions() as session:
                try:
                    return await reserve_canary_attempt(
                        session, replace(request, attempt_key=uuid4())
                    )
                except CanaryDenied:
                    return None

        results = await asyncio.gather(*(attempt(i) for i in range(9)))
        assert sorted(x for x in results if x is not None) == [1, 2, 3, 4, 5, 6]
        assert results.count(None) == 3
        async with sessions() as session:
            scope = await session.get(ReplyCanaryScope, "synthetic")
            count = await session.scalar(select(func.count()).select_from(ReplyCanaryAttempt))
            assert scope.attempts_reserved == count == 6
        async with sessions() as session:
            with pytest.raises(CanaryDenied, match="canary_admission_closed"):
                await reserve_canary_attempt(session, replace(request, kind="notification"))


@pytest.mark.anyio
async def test_expired_or_wrong_body_never_consumes_attempt():
    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module("app.models." + module.name)
    async with _postgres_sessions() as sessions:
        account = uuid4()
        request = CanaryAttemptRequest(
            workspace_ref="synthetic",
            execution_id="exact-execution",
            attempt_key=uuid4(),
            kind="sequence",
            principal_subject="owner",
            account_id=account,
            dialog_ref="one",
            body_sha256="c" * 64,
            notification_destination=None,
            notification_template=None,
        )
        async with sessions() as session:
            session.add(
                ReplyCanaryScope(
                    workspace_ref="synthetic",
                    execution_id="exact-execution",
                    state="active",
                    principal_subject="owner",
                    sequence_grants=[f"{account}:one:{'a' * 64}"],
                    notification_destination="owner-only",
                    notification_template="stop-v1",
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                    max_attempts=6,
                    attempts_reserved=0,
                    scope_sha256="b" * 64,
                )
            )
            await session.commit()
        async with sessions() as session:
            with pytest.raises(CanaryDenied, match="canary_scope_mismatch"):
                await reserve_canary_attempt(session, request)
        async with sessions() as session:
            session.add(
                ReplyCanaryScope(
                    workspace_ref="expired",
                    execution_id="expired-execution",
                    state="active",
                    principal_subject="owner",
                    sequence_grants=[f"{account}:one:{'a' * 64}"],
                    notification_destination="owner-only",
                    notification_template="stop-v1",
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                    max_attempts=6,
                    attempts_reserved=0,
                    scope_sha256="b" * 64,
                )
            )
            await session.commit()
        async with sessions() as session:
            with pytest.raises(CanaryDenied, match="canary_admission_closed"):
                await reserve_canary_attempt(
                    session,
                    replace(
                        request,
                        workspace_ref="expired",
                        execution_id="expired-execution",
                        body_sha256="a" * 64,
                    ),
                )
        async with sessions() as session:
            scope = await session.get(ReplyCanaryScope, "synthetic")
            assert scope.attempts_reserved == 0


@pytest.mark.anyio
async def test_http_send_requires_committed_admission_for_each_retry(monkeypatch):
    import app.core.config as config

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(gate_reply_sequence_canary_enabled=True),
    )
    reservations = []
    requests = []

    class Gate:
        async def before(self, payload):
            return time.monotonic()

        async def failed(self, payload, retry_after):
            assert retry_after == 1

        async def reserve_canary(self, intent, payload):
            if not intent.matches(payload):
                raise TelegramApiError(error_code=403, description="Canary payload mismatch")
            reservations.append((intent, payload.copy()))

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "synthetic rate limit",
                    "parameters": {"retry_after": 1},
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    gate = Gate()
    register_gate("123456789", gate)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            telegram = TelegramClient("123456789:synthetic", http_client=client)
            with pytest.raises(TelegramApiError, match="Canary admission closed"):
                await telegram.send_plain_message(chat_id="owner", text="stop")
            assert not requests and not reservations
            intent = CanaryDelivery(
                kind="notification",
                principal_subject="owner-subject",
                account_id=None,
                dialog_ref=None,
                body_sha256=None,
                destination="owner",
                notification_template="reply_sequence_stop_v1",
                notification_text_sha256=hashlib.sha256(b"stop").hexdigest(),
            )
            with canary_delivery(intent):
                with pytest.raises(TelegramApiError, match="Canary payload mismatch"):
                    await telegram.send_plain_message(chat_id="other", text="stop")
            assert not requests
            with canary_delivery(intent):
                with pytest.raises(TelegramApiError) as rate_limited:
                    await telegram.send_plain_message(chat_id="owner", text="stop")
                assert rate_limited.value.error_code == 429
                await telegram.send_plain_message(chat_id="owner", text="stop")
            assert len(reservations) == len(requests) == 2
    finally:
        unregister_gate("123456789", gate)
