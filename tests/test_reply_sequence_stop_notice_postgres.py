"""Only a durable, scope-matched inbound stop can produce a notice preview."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.infrastructure.reply_sequence_stop_notice import read_reply_stop_notice
from app.models.domain import AuditLog, ReplyDraft
from sqlalchemy import select

from test_dialog_snapshots_postgres import receive
from test_reply_sends_postgres import _command, _postgres_sessions, _seed_reply
from test_reply_sequence_tailstop_postgres import queue


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_notice_has_one_stable_id_and_no_message_or_recipient_data() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, datetime.now(UTC) - timedelta(hours=1))
        scope = _command(seed, seed.draft_ids[0]).principal.access_scope
        for update in (200, 200, 201):
            await receive(sessions, seed, update_id=update, content={"text": "synthetic body"})
        events = select(AuditLog.id).where(AuditLog.action == "reply_sequence_stopped")
        async with sessions() as session:
            (event_id,) = (await session.scalars(events)).all()
            first = await read_reply_stop_notice(session, event_id=event_id, scope=scope)
            assert first is not None
            assert asdict(first) == {
                "idempotency_key": event_id,
                "text": "Gate остановил серию после нового события в диалоге.",
            }
            wrong_scopes = (
                replace(scope, workspace_id="another-workspace"),
                replace(scope, account_ids=frozenset({uuid4()})),
                replace(scope, project_ids=frozenset({uuid4()})),
            )
            for wrong in wrong_scopes:
                assert await read_reply_stop_notice(session, event_id=event_id, scope=wrong) is None
        async with sessions() as session:
            assert await read_reply_stop_notice(session, event_id=event_id, scope=scope) == first


@pytest.mark.anyio
async def test_missing_ingress_receipt_and_unrelated_stop_cannot_create_notice() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, datetime.now(UTC) - timedelta(hours=1))
        scope = _command(seed, seed.draft_ids[0]).principal.access_scope
        await receive(sessions, seed, update_id=203)
        async with sessions.begin() as session:
            event = (await session.scalars(select(AuditLog).where(
                AuditLog.action == "reply_sequence_stopped",
            ))).one()
            event_id = event.id
            event.metadata_ = {**event.metadata_, "reason": "other_work_send"}
            await session.flush()
            assert await read_reply_stop_notice(session, event_id=event_id, scope=scope) is None

            missing = str(uuid4())
            event.metadata_ = {**event.metadata_, "reason": "context_changed", "event_ref": missing}
            drafts = (await session.scalars(select(ReplyDraft).where(
                ReplyDraft.id.in_(seed.draft_ids),
            ))).all()
            for draft in drafts:
                context = draft.prompt_context
                draft.prompt_context = {**context, "reply_sequence": {
                    **context["reply_sequence"], "stop_event_ref": missing,
                }}
            await session.flush()
            assert await read_reply_stop_notice(session, event_id=event_id, scope=scope) is None
