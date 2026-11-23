"""Delivery claims survive process restarts and lost acknowledgments."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.infrastructure.reply_stop_inbox_delivery import (
    ReplyStopInboxClient,
    deliver_reply_stop_notice,
)
from app.models.domain import AuditLog
from sqlalchemy import func, select

from test_dialog_snapshots_postgres import receive
from test_reply_sends_postgres import _command, _postgres_sessions, _seed_reply
from test_reply_sequence_tailstop_postgres import queue

_PRIVATE_TARGET = {
    "full_name": "markmalina/mark",
    "id": 1336157811,
    "private": True,
    "archived": False,
    "has_issues": True,
    "owner": {"id": 23397294},
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_one_post_across_overlapping_calls_restart_and_readback(lost_ack):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, datetime.now(UTC) - timedelta(hours=1))
        await receive(sessions, seed, update_id=9)
        scope = _command(seed, seed.draft_ids[0]).principal.access_scope
        async with sessions() as session:
            event_id = (await session.scalars(select(AuditLog.id).where(
                AuditLog.action == "reply_sequence_stopped",
            ))).one()

        posts = []
        stored = []
        only_legacy = lost_ack
        posted, release_post = asyncio.Event(), asyncio.Event()

        async def route(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "api.github.com"
            if request.url.path == "/repos/markmalina/mark":
                return httpx.Response(200, json=_PRIVATE_TARGET)
            assert request.url.path == "/repos/markmalina/mark/issues"
            if request.method == "GET":
                legacy = [{**issue, "user": {"login": "github-actions[bot]"}} for issue in stored]
                return httpx.Response(200, json=legacy + ([] if only_legacy else stored))
            assert request.method == "POST"
            payload = json.loads(request.content)
            posts.append(payload)
            async with sessions() as session:
                claim_count = await session.scalar(select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == "reply_stop_notice_claimed",
                ))
                assert claim_count == 1
            issue = {
                "number": 17, "html_url": "https://github.com/markmalina/mark/issues/17",
                "created_at": "2026-11-23T08:00:00Z", "body": payload["body"],
                "user": {"login": "synthetic-owner-notice[bot]"},
            }
            stored.append(issue)
            posted.set()
            await asyncio.wait_for(release_post.wait(), 10)
            if lost_ack:
                raise httpx.ReadTimeout("synthetic lost response")
            return httpx.Response(201, json=issue)

        async def deliver():
            async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
                inbox = ReplyStopInboxClient(
                    http, "synthetic-token", app_slug="synthetic-owner-notice",
                )
                return await deliver_reply_stop_notice(
                    sessions, event_id=event_id, scope=scope, inbox=inbox,
                )

        original = asyncio.create_task(deliver())
        try:
            await asyncio.wait_for(posted.wait(), 10)
            overlapping = await deliver()
        finally:
            release_post.set()
        original_result = await original
        assert overlapping.state == ("unknown" if lost_ack else "published")
        assert original_result.state == ("unknown" if lost_ack else "published")
        assert len(posts) == 1
        assert posts[0]["body"] == (
            "Gate остановил серию после нового события в диалоге."
            f"\n\n@markmalina\n\n<!-- gate-reply-stop-notice:v1 {event_id} -->"
        )
        if lost_ack:
            assert (await deliver()).state == "unknown"
            only_legacy = False
        after_restart = await deliver()
        assert after_restart.state == "published" and after_restart.issue_number == 17
        assert len(posts) == 1
        async with sessions() as session:
            published = (await session.scalars(select(AuditLog).where(
                AuditLog.action == "reply_stop_notice_published",
            ))).one()
            assert published.metadata_["issue_number"] == 17


@pytest.mark.anyio
async def test_public_target_denial_precedes_claim_and_post():
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, datetime.now(UTC) - timedelta(hours=1))
        await receive(sessions, seed, update_id=10)
        scope = _command(seed, seed.draft_ids[0]).principal.access_scope
        async with sessions() as session:
            event_id = (await session.scalars(select(AuditLog.id).where(
                AuditLog.action == "reply_sequence_stopped",
            ))).one()

        def route(request):
            assert request.method == "GET"
            assert request.url.path == "/repos/markmalina/mark"
            return httpx.Response(200, json={**_PRIVATE_TARGET, "private": False})

        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
            inbox = ReplyStopInboxClient(http, "synthetic", app_slug="synthetic")
            result = await deliver_reply_stop_notice(
                sessions, event_id=event_id, scope=scope, inbox=inbox,
            )
            assert result.state == "route_unavailable"
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "reply_stop_notice_claimed",
            )) == 0


@pytest.mark.anyio
async def test_interruption_after_claim_without_a_visible_issue_stays_unknown():
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, datetime.now(UTC) - timedelta(hours=1))
        await receive(sessions, seed, update_id=11)
        scope = _command(seed, seed.draft_ids[0]).principal.access_scope
        async with sessions() as session:
            event_id = (await session.scalars(select(AuditLog.id).where(
                AuditLog.action == "reply_sequence_stopped",
            ))).one()

        def route(request):
            assert request.method == "GET"
            if request.url.path == "/repos/markmalina/mark":
                return httpx.Response(200, json=_PRIVATE_TARGET)
            assert request.url.path == "/repos/markmalina/mark/issues"
            return httpx.Response(200, json=[])

        class InterruptedInbox(ReplyStopInboxClient):
            async def post(self, **_kwargs):
                raise asyncio.CancelledError

        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
            interrupted = InterruptedInbox(
                http, "synthetic", app_slug="synthetic",
            )
            with pytest.raises(asyncio.CancelledError):
                await deliver_reply_stop_notice(
                    sessions, event_id=event_id, scope=scope, inbox=interrupted,
                )
            restarted = ReplyStopInboxClient(http, "synthetic", app_slug="synthetic")
            result = await deliver_reply_stop_notice(
                sessions, event_id=event_id, scope=scope, inbox=restarted,
            )
            assert result.state == "unknown" and result.issue_number is None
