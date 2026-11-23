"""Provision a single private target in the synthetic publisher path."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.infrastructure.reply_sequence_state import scoped_sequence_status
from app.models.domain import AuditLog
from app.worker.reply_stop_notice_publisher import (
    automatic_publisher_config,
    publish_once,
    publish_pending_page,
    publisher_config,
)
from sqlalchemy import select

from test_dialog_snapshots_postgres import receive
from test_reply_sends_postgres import _postgres_sessions, _seed_reply
from test_reply_sequence_tailstop_postgres import queue
from test_watch_app_auth import private_key

_PREFIX = "GATE_REPLY_STOP_NOTICE_"
_TARGET = {
    "id": 1336157811, "full_name": "markmalina/mark", "private": True,
    "archived": False, "has_issues": True, "owner": {"id": 23397294},
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _environment(account_id, project_id, key="synthetic"):
    settings = {
        "ENABLED": "true", "APP_ID": "5100", "APP_INSTALLATION_ID": "5200",
        "APP_SLUG": "synthetic-reply-stop", "APP_PRIVATE_KEY": key,
        "ACCOUNT_IDS": str(account_id), "PROJECT_IDS": str(project_id),
    }
    return {_PREFIX + name: value for name, value in settings.items()}


@pytest.mark.parametrize(("name", "value"), [
    ("GATE_WATCH_APP_ID", "5100"),
    ("GATE_WATCH_APP_INSTALLATION_ID", "5200"),
    ("GATE_WATCH_APP_SLUG", "synthetic-reply-stop"),
    (_PREFIX + "ENABLED", "false"),
    (_PREFIX + "ACCOUNT_IDS", ""),
    (_PREFIX + "PROJECT_IDS", "invalid"),
    (_PREFIX + "APP_PRIVATE_KEY", ""),
])
def test_publisher_defaults_off_and_rejects_watch_publisher(name, value):
    assert publisher_config({}) is None
    env = _environment(uuid4(), uuid4())
    assert publisher_config(env) is not None
    assert publisher_config(env | {name: value}) is None


@pytest.mark.parametrize(
    "since", ["", "2026-11-23", "2026-11-23T08:00:00+03:00", "2026-02-30T00:00:00Z"],
)
def test_automatic_publisher_requires_explicit_utc_activation(since):
    env = _environment(uuid4(), uuid4())
    assert automatic_publisher_config(env | {_PREFIX + "AUTO_AFTER": since}) is None
    assert automatic_publisher_config(
        env | {_PREFIX + "AUTO_AFTER": "2026-11-23T09:00:00Z"},
    ) is not None


@pytest.mark.anyio
@pytest.mark.parametrize(("broad_token", "auto"), [(False, False), (True, False), (False, True)])
async def test_full_synthetic_publisher_uses_only_private_mark_target(broad_token, auto):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, datetime.now(UTC) - timedelta(hours=1))
        await receive(sessions, seed, update_id=23)
        if auto:
            await receive(sessions, seed, update_id=23)
        async with sessions() as session:
            stops = select(AuditLog.id, AuditLog.created_at).where(
                AuditLog.action == "reply_sequence_stopped",
            )
            event_id, stopped_at = (await session.execute(stops)).one()
        config = publisher_config(_environment(seed.account_id, seed.project_id, private_key()))
        assert config is not None
        calls = []
        token = "synthetic-dedicated-installation-token"

        def handler(request):
            path = request.url.path
            calls.append((request.method, path))
            if path == "/app/installations/5200":
                assert request.method == "GET"
                return httpx.Response(200, json={
                    "id": 5200, "app_id": 5100, "app_slug": "synthetic-reply-stop",
                    "account": {"id": 23397294}, "suspended_at": None,
                    "repository_selection": "selected",
                    "permissions": {"issues": "write", "metadata": "read"},
                })
            if path == "/app/installations/5200/access_tokens":
                assert request.method == "POST"
                assert json.loads(request.content) == {
                    "repository_ids": [1336157811], "permissions": {"issues": "write"},
                }
                permissions = {"issues": "write", "metadata": "read"}
                if broad_token:
                    permissions["contents"] = "write"
                return httpx.Response(201, json={"token": token, "permissions": permissions})
            assert request.headers["Authorization"] == f"Bearer {token}"
            if path == "/installation/repositories":
                return httpx.Response(200, json={"total_count": 1, "repositories": [_TARGET]})
            if path == "/repos/markmalina/mark":
                return httpx.Response(200, json=_TARGET)
            assert path == "/repos/markmalina/mark/issues" and request.method == "POST"
            payload = json.loads(request.content)
            assert payload["body"].splitlines().count("@markmalina") == 1
            return httpx.Response(201, json={
                "number": 7, "html_url": "https://github.com/markmalina/mark/issues/7",
                "created_at": "2026-11-23T08:00:00Z", "body": payload["body"],
                "user": {"login": "synthetic-reply-stop[bot]"},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            unavailable = await publish_once(sessions, http, config, event_id=uuid4())
            assert unavailable.state == "route_unavailable" and calls == []
            if auto:
                assert await publish_pending_page(
                    sessions, http, config, since=stopped_at + timedelta(seconds=1),
                ) is None
                assert calls == []
                foreign = publisher_config(_environment(uuid4(), seed.project_id))
                assert foreign is not None
                assert await publish_pending_page(
                    sessions, http, foreign, since=stopped_at,
                ) is None
                assert calls == []
                older = stopped_at - timedelta(seconds=1)
                async with sessions.begin() as session:
                    session.add_all([
                        AuditLog(
                            actor="gate", action="reply_sequence_stopped",
                            account_id=seed.account_id, project_id=seed.project_id,
                            created_at=older,
                            metadata_={"reason": "context_changed", "sequence_id": str(uuid4())},
                        )
                        for _ in range(4)
                    ])
                cursor = await publish_pending_page(sessions, http, config, since=older)
                assert cursor is not None and calls == []
                assert await publish_pending_page(
                    sessions, http, config, since=older, cursor=cursor,
                ) is None
                assert calls.count(("POST", "/repos/markmalina/mark/issues")) == 1
                first_calls = calls.copy()
                assert await publish_pending_page(
                    sessions, http, config, since=stopped_at,
                ) is None
                assert calls == first_calls
                async with sessions() as session:
                    (status,) = await scoped_sequence_status(
                        session, scope=config.scope, task_id=seed.task_id,
                    )
                assert status["notification_status"] == "published"
                return
            result = await publish_once(sessions, http, config, event_id=event_id)
        if broad_token:
            assert result.state == "route_unavailable"
            assert len(calls) == 2
        else:
            assert result.state == "published" and result.issue_number == 7
            assert calls.count(("POST", "/repos/markmalina/mark/issues")) == 1
