"""Provision a single private target in the synthetic publisher path."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.models.domain import AuditLog
from app.worker.reply_stop_notice_publisher import publish_once, publisher_config
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


@pytest.mark.anyio
@pytest.mark.parametrize("broad_token", [False, True])
async def test_full_synthetic_publisher_uses_only_private_mark_target(broad_token):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, datetime.now(UTC) - timedelta(hours=1))
        await receive(sessions, seed, update_id=23)
        async with sessions() as session:
            event_id = (await session.scalars(select(AuditLog.id).where(
                AuditLog.action == "reply_sequence_stopped",
            ))).one()
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
            return httpx.Response(201, json={
                "number": 7, "html_url": "https://github.com/markmalina/mark/issues/7",
                "created_at": "2026-11-23T08:00:00Z", "body": payload["body"],
                "user": {"login": "synthetic-reply-stop[bot]"},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            unavailable = await publish_once(sessions, http, config, event_id=uuid4())
            assert unavailable.state == "route_unavailable" and calls == []
            result = await publish_once(sessions, http, config, event_id=event_id)
        if broad_token:
            assert result.state == "route_unavailable"
            assert len(calls) == 2
        else:
            assert result.state == "published" and result.issue_number == 7
            assert calls.count(("POST", "/repos/markmalina/mark/issues")) == 1
