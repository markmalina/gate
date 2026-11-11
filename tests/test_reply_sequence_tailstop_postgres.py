"""S4 actual SQL/ingress/worker and synthetic provider, including process races."""

import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from app.application.mcp_inbox import get_reply_context
from app.application.reply_sends import ReplySendError
from app.infrastructure import reply_sends, reply_sequence_state
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequence_state import scoped_sequence_status
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.models.domain import AuditLog, Message, ReplyDraft, SendAttempt
from app.worker.reply_sequences import _discover_sequence_rows, run_reply_sequence_once
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from test_dialog_snapshots_postgres import receive
from test_reply_final_admission_postgres import envelope
from test_reply_sends_postgres import (
    _command,
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _seed_reply,
    _sequence_command,
    _settings,
    _wait_for_backend_lock,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def clock(monkeypatch):
    values = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return values[0]

    monkeypatch.setattr(reply_sends, "datetime", Clock)
    monkeypatch.setattr(reply_sequence_state, "datetime", Clock)
    return values


class Recorder(_LiteralRecordingTelegram):
    async def send_plain_message(self, **kwargs):
        self.plain_calls.append(kwargs)
        return {"ok": True, "result": {"message_id": 1000 + len(self.plain_calls)}}


async def queue(sessions, seed, now):
    async with sessions() as session:
        result = await SqlReplySequenceWriter(session, now=now - timedelta(hours=2)).queue(
            _sequence_command(seed, seed.draft_ids),
        )
        await session.commit()
        return result


async def tick(sessions, telegram, clock):
    return await run_reply_sequence_once(
        settings=_settings(), session_factory=sessions, telegram=telegram, now=clock[0],
    )


async def status(sessions, seed):
    async with sessions() as session:
        return (await scoped_sequence_status(
            session, scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
            task_id=seed.task_id,
        ))[0]


@pytest.mark.anyio
@pytest.mark.parametrize("size", [2, 50])
async def test_exact_order_interval_echo_and_restart(size, clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=size)
        await queue(sessions, seed, clock[0])
        telegram = Recorder()
        for position in range(size):
            assert await tick(sessions, telegram, clock) == 1
            # Confirmed own echo, duplicate webhook, delayed repetition with a
            # different update id. All have exact provider identity, no text guess.
            for update_id in (100 + position * 2, 100 + position * 2, 101 + position * 2):
                await receive(sessions, seed, update_id=update_id, owner=True, content={
                    "message_id": 1001 + position, "text": f"Approved draft {position + 1}",
                })
            if position + 1 < size:
                assert await tick(sessions, telegram, clock) == 0
                clock[0] += timedelta(seconds=59)
                assert await tick(sessions, telegram, clock) == 0
                clock[0] += timedelta(seconds=1)
        assert [call["text"] for call in telegram.plain_calls] == [
            f"Approved draft {index + 1}" for index in range(size)
        ]
        result = await status(sessions, seed)
        assert result["state"] == "sent" and result["sent_count"] == size
        assert result["remaining_count"] == 0 and not result["auto_resume"]
        assert await tick(sessions, telegram, clock) == 0
        async with sessions() as session:
            attempts = (await session.scalars(select(SendAttempt).order_by(
                SendAttempt.finished_at,
            ))).all()
            assert len(attempts) == size
            assert all((b.finished_at - a.finished_at).total_seconds() >= 60
                       for a, b in zip(attempts, attempts[1:], strict=False))


@pytest.mark.anyio
@pytest.mark.parametrize("content,owner,kind", [
    ({"text": "new"}, False, "business_message"),
    ({"voice": {"file_id": "synthetic"}}, False, "business_message"),
    ({"photo": [{"file_id": "synthetic"}]}, False, "business_message"),
    ({"document": {"file_id": "synthetic"}}, False, "business_message"),
    ({"text": "Approved draft 1"}, True, "business_message"),
    ({"text": "edit"}, True, "edited_business_message"),
    ({"message_ids": [1001]}, False, "deleted_business_messages"),
])
async def test_foreign_event_stops_immediately_without_worker(content, owner, kind, clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=50)
        queued = await queue(sessions, seed, clock[0])
        telegram = Recorder()
        assert await tick(sessions, telegram, clock) == 1
        await receive(sessions, seed, update_id=7, content=content, owner=owner, kind=kind)
        # Readback BEFORE another worker, also after repeated event/command.
        result = await status(sessions, seed)
        assert result["sequence_id"] == str(queued.sequence_id)
        assert result["state"] == "stopped" and result["stop_reason"] == "context_changed"
        assert result["sent_count"] == 1 and result["remaining_count"] == 49
        assert result["items"][0]["provider_message_id"] == "1001"
        assert all(item["state"] == "stopped" for item in result["items"][1:])
        assert result["notification_status"] == "route_unavailable" and not result["auto_resume"]
        await receive(sessions, seed, update_id=7, content=content, owner=owner, kind=kind)
        async with sessions() as session:
            with pytest.raises(Exception) as denied:
                await SqlReplySequenceWriter(session).queue(_sequence_command(seed, seed.draft_ids))
            assert denied.value.code in ("sequence_already_queued", "draft_closed")
        clock[0] += timedelta(days=1)
        assert await tick(sessions, telegram, clock) == 0
        assert len(telegram.plain_calls) == 1
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "reply_sequence_stopped",
            )) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_early_unproven_echo_preserves_inflight_and_never_resumes(lost_ack, clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, clock[0])

        class Early(Recorder):
            async def send_plain_message(self, **kwargs):
                await receive(sessions, seed, owner=True, update_id=4,
                              content={"message_id": 1001, "text": kwargs["text"]})
                readback = await status(sessions, seed)
                assert readback["state"] == "stopped"
                assert readback["ambiguous_count"] == 1
                assert readback["items"][0]["state"] == "sending"
                result = await super().send_plain_message(**kwargs)
                if lost_ack:
                    raise httpx.ReadTimeout("synthetic lost response")
                return result

        telegram = Early()
        assert await tick(sessions, telegram, clock) == (0 if lost_ack else 1)
        result = await status(sessions, seed)
        assert result["state"] == "stopped" and not result["auto_resume"]
        assert result["items"][0]["state"] == ("unknown" if lost_ack else "sent")
        clock[0] += timedelta(hours=1)
        for _ in range(2):
            assert await tick(sessions, telegram, clock) == 0
        assert len(telegram.plain_calls) == 1


@pytest.mark.anyio
async def test_scoped_status_and_other_account_chat_isolation(clock):
    async with _postgres_sessions() as sessions:
        first = await _seed_reply(sessions, draft_count=2)
        second = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, first, clock[0])
        await queue(sessions, second, clock[0])
        await receive(sessions, first, chat_id="77777", update_id=1)
        assert (await status(sessions, first))["state"] == "scheduled"
        await receive(sessions, first, update_id=2)
        assert (await status(sessions, first))["state"] == "stopped"
        assert (await status(sessions, second))["state"] == "scheduled"
        async with sessions() as session:
            scope = _command(first, first.draft_ids[0]).principal.access_scope
            for denied_scope in (replace(scope, account_ids=frozenset({uuid4()})),
                                 replace(scope, project_ids=frozenset({uuid4()})),
                                 replace(scope, workspace_id="another-workspace")):
                assert await scoped_sequence_status(session, scope=denied_scope,
                                                    task_id=first.task_id) == []
            context = await get_reply_context(session=session, scope=scope,
                                              task_id=first.task_id, limit=20)
            assert context["reply_sequences"][0]["state"] == "stopped"
            from app.api.mcp_routes import _format_reply_context_text
            from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
            from jsonschema import Draft202012Validator
            Draft202012Validator(
                MCP_TOOL_OUTPUT_SCHEMAS["gate_get_reply_context"],
            ).validate(context)
            rendered = _format_reply_context_text(context)
            assert "stopped" in rendered and "Auto-resume: false" in rendered
            assert "route_unavailable" in rendered
        assert await tick(sessions, Recorder(), clock) == 1


@pytest.mark.anyio
async def test_projected_confirmed_echo_is_own_progress_but_other_work_is_not(clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=4)
        # Two series items and one separately approved Work draft.
        sequence_seed = replace(seed, draft_ids=seed.draft_ids[:3])
        await queue(sessions, sequence_seed, clock[0])
        telegram = Recorder()
        assert await tick(sessions, telegram, clock) == 1
        await receive(sessions, seed, owner=True, update_id=51,
                      content={"message_id": 1001, "text": "Approved draft 1"})
        async with sessions() as session:
            message = await session.scalar(select(Message).where(
                Message.source == "gate-approved-draft",
            ))
            # Same identity a real asynchronous text projector produces.
            session.add(Message(
                bot_id=message.bot_id, account_id=message.account_id, project_id=message.project_id,
                direction="outbound", source="telegram", event_type="business_message",
                chat_id=message.chat_id, sender_id=message.sender_id, text=message.text,
                business_connection_id=message.business_connection_id,
                telegram_message_id=1001, telegram_update_id=51, payload={"canonical_source": True},
            ))
            await session.commit()
        clock[0] += timedelta(seconds=60)
        assert await tick(sessions, telegram, clock) == 1
        # Another Work gets a fresh review, then sends a separately approved item.
        # It is deliberately not allowed to inherit this series' own identity.
        from app.infrastructure.dialog_snapshots import dialog_snapshot
        from app.models.domain import Conversation
        async with sessions() as session:
            draft = await session.get(ReplyDraft, seed.draft_ids[3])
            conv = await session.get(Conversation, seed.conversation_id)
            draft.prompt_context = {"dialog_snapshot": await dialog_snapshot(
                session, scope=_command(seed, draft.id).principal.access_scope, conversation=conv,
            )}
            await session.commit()
            assert (await SqlTelegramReplySender(
                session=session, telegram=telegram, settings=_settings(),
            ).send(_command(seed, draft.id))).sent
        stopped = await status(sessions, seed)
        assert stopped["state"] == "stopped" and stopped["stop_reason"] == "other_work_send"
        assert stopped["sent_count"] == 2 and stopped["remaining_count"] == 1
        # Its echo is not a member of the series.
        await receive(sessions, seed, owner=True, update_id=52,
                      content={"message_id": 1003, "text": "Approved draft 1"})
        assert len(telegram.plain_calls) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["event", "gap"])
async def test_two_final_workers_wait_for_independent_process_committed_stop(kind, clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, clock[0])
        engine = sessions.kw["bind"]
        command = _command(seed, seed.draft_ids[0])
        telegram = Recorder()
        async with sessions() as reserve_session:
            sender = SqlTelegramReplySender(session=reserve_session, telegram=telegram,
                                            settings=_settings())
            intent = await sender._preflight(command)
            probe = await sender._probe_connection(intent)
            reservation = await sender._reserve(command=command, intent=intent, probe=probe)
        async with engine.connect() as a, engine.connect() as b:
            async with AsyncSession(a) as sa, AsyncSession(b) as sb:
                pids = []
                for session in (sa, sb):
                    pids.append(await session.scalar(text("select pg_backend_pid()")))
                    schema = await session.scalar(text("select current_schema()"))
                    await session.rollback()
                child = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(Path(__file__).parent / "support/dialog_ingress_process.py"),
                    schema, seed.business_connection_id, seed.recipient_chat_id, kind,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                tasks = []
                try:
                    ready = await asyncio.wait_for(child.stdout.readline(), 15)
                    assert ready.startswith(b"READY "), await child.stderr.read()
                    assert len(set(pids + [int(ready.split()[1])])) == 3
                    for session in (sa, sb):
                        tasks.append(asyncio.create_task(SqlTelegramReplySender(
                            session=session, telegram=telegram, settings=_settings(),
                        )._admit_dispatch(command=command, reservation=reservation)))
                    for pid in pids:
                        await _wait_for_backend_lock(sessions, backend_pid=pid)
                    child.stdin.write(b"COMMIT\n")
                    await child.stdin.drain()
                    assert await asyncio.wait_for(child.stdout.readline(), 15) == b"COMMITTED\n"
                    assert await asyncio.wait_for(child.wait(), 15) == 0
                    for task in tasks:
                        with pytest.raises(ReplySendError):
                            await asyncio.wait_for(task, 15)
                    assert telegram.plain_calls == []
                    result = await status(sessions, seed)
                    assert result["state"] == "stopped" and result["ambiguous_count"] == 1
                finally:
                    if child.returncode is None:
                        child.kill()
                        await child.wait()
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_stop_and_discovery_do_not_truncate_beyond_500_rows(clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=50)
        await queue(sessions, seed, clock[0])
        async with sessions() as session:
            originals = (await session.scalars(select(ReplyDraft))).all()
            for _group in range(11):
                group_id = str(uuid4())
                for original in originals:
                    meta = dict(original.prompt_context["reply_sequence"])
                    meta["id"] = group_id
                    session.add(ReplyDraft(
                        account_id=original.account_id, project_id=original.project_id,
                        reply_task_id=original.reply_task_id,
                        conversation_id=original.conversation_id,
                        trigger_message_id=original.trigger_message_id, status=original.status,
                        source="synthetic-backlog", draft_text=original.draft_text,
                        prompt_context={**original.prompt_context, "reply_sequence": meta},
                    ))
            await session.commit()
        assert len(await _discover_sequence_rows(sessions)) == 600
        await receive(sessions, seed)
        async with sessions() as session:
            metadata = (await session.scalars(select(ReplyDraft.prompt_context))).all()
            assert len(metadata) == 600
            assert all(row["reply_sequence"]["state"] == "stopped" for row in metadata)


@pytest.mark.anyio
@pytest.mark.parametrize("foreign_event", [False, True])
async def test_two_whole_workers_preserve_inflight_receipt(foreign_event, clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, clock[0])
        started, release = asyncio.Event(), asyncio.Event()

        class Slow(Recorder):
            async def send_plain_message(self, **kwargs):
                started.set()
                await asyncio.wait_for(release.wait(), 10)
                return await super().send_plain_message(**kwargs)

        telegram = Slow()
        first = asyncio.create_task(tick(sessions, telegram, clock))
        try:
            await asyncio.wait_for(started.wait(), 10)
            assert await asyncio.wait_for(tick(sessions, telegram, clock), 10) == 0
            if foreign_event:
                await receive(sessions, seed, update_id=10)
                result = await status(sessions, seed)
                assert result["state"] == "stopped"
                assert result["items"][0]["state"] == "sending"
            release.set()
            assert await asyncio.wait_for(first, 10) == 1
        finally:
            release.set()
            if not first.done():
                first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        assert len(telegram.plain_calls) == 1
        clock[0] += timedelta(seconds=60)
        assert await tick(sessions, telegram, clock) == (0 if foreign_event else 1)
        result = await status(sessions, seed)
        assert result["items"][0]["state"] == "sent"
        assert result["state"] == ("stopped" if foreign_event else "sent")


@pytest.mark.anyio
async def test_direct_item_cannot_skip_predecessor_or_interval(clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, clock[0])
        telegram = Recorder()
        async with sessions() as session:
            with pytest.raises(ReplySendError) as denied:
                await SqlTelegramReplySender(session=session, telegram=telegram,
                                             settings=_settings()).send(
                    _command(seed, seed.draft_ids[1]),
                )
            assert denied.value.code == "context_changed"
        assert telegram.plain_calls == []
        assert await tick(sessions, telegram, clock) == 1
        async with sessions() as session:
            with pytest.raises(ReplySendError) as denied:
                await SqlTelegramReplySender(session=session, telegram=telegram,
                                             settings=_settings()).send(
                    _command(seed, seed.draft_ids[1]),
                )
            assert denied.value.code == "sequence_not_due"
        assert len(telegram.plain_calls) == 1
        clock[0] += timedelta(seconds=60)
        assert await tick(sessions, telegram, clock) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["unscoped", "disabled", "unknown_connection"])
async def test_unavailable_scope_stops_durably_and_preserves_sticky_gap(failure, clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        other = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, clock[0])
        await queue(sessions, other, clock[0])
        payload = {
            "unscoped": {"business_message": {"voice": {}}},
            "disabled": {"business_connection": {
                "id": seed.business_connection_id, "is_enabled": False,
            }},
            "unknown_connection": {"business_message": {
                "business_connection_id": "unmapped", "chat": {"id": 12345}, "text": "new",
            }},
        }[failure]
        await envelope(sessions, payload, 61)
        assert (await status(sessions, seed))["state"] == "stopped"
        assert (await status(sessions, other))["state"] == (
            "scheduled" if failure == "disabled" else "stopped"
        )
        await envelope(sessions, {"business_connection": {
            "id": seed.business_connection_id, "is_enabled": True,
        }}, 62)
        async with sessions() as session:
            context = await get_reply_context(
                session=session, scope=_command(seed, seed.draft_ids[0]).principal.access_scope,
                task_id=seed.task_id, limit=20,
            )
            assert not context["context_ready"] and context["context_reason"] == "known_ingress_gap"
        clock[0] += timedelta(hours=2)
        assert await tick(sessions, Recorder(), clock) == (1 if failure == "disabled" else 0)


@pytest.mark.anyio
async def test_confirmed_echo_after_stop_does_not_clear_latch_or_hide_manual_message(clock):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await queue(sessions, seed, clock[0])
        telegram = Recorder()
        assert await tick(sessions, telegram, clock) == 1
        # Incoming identical text and even same numeric ID is not an outbound echo.
        await receive(sessions, seed, update_id=8, content={
            "message_id": 1001, "text": "Approved draft 1",
        })
        before = await status(sessions, seed)
        await receive(sessions, seed, update_id=9, owner=True, content={
            "message_id": 1001, "text": "Approved draft 1",
        })
        assert (await status(sessions, seed)) == before
        clock[0] += timedelta(days=1)
        assert await tick(sessions, telegram, clock) == 0
        assert len(telegram.plain_calls) == 1
