"""Synthetic PostgreSQL concurrency checks for the irreversible canary cap."""

import asyncio
import importlib
import pkgutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import app.models
import pytest
from app.infrastructure.reply_sequence_canary import (
    CanaryAttemptRequest,
    CanaryDenied,
    reserve_canary_attempt,
)
from app.models.reply_sequences import ReplyCanaryAttempt, ReplyCanaryScope
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
            scope = await session.get(ReplyCanaryScope, "synthetic")
            scope.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        async with sessions() as session:
            with pytest.raises(CanaryDenied, match="canary_admission_closed"):
                await reserve_canary_attempt(session, replace(request, body_sha256="a" * 64))
        async with sessions() as session:
            scope = await session.get(ReplyCanaryScope, "synthetic")
            assert scope.attempts_reserved == 0
