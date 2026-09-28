"""Real PostgreSQL regressions for PR #720 review 5332137616; no provider effects."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.api import mcp_routes
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS, SEQUENCE_DETAIL_SCHEMA
from app.application.reply_sequences import ReplySequenceError
from app.core.mcp_security import AccessScope
from app.infrastructure.reply_sequence_context import approval_context_state
from app.models.domain import (
    Account,
    Conversation,
    Message,
    ReplyDraft,
    ReplyTask,
    SendAttempt,
    TelegramOutboxEvent,
    TelegramReceipt,
)
from app.models.reply_sequences import ReplySequence, ReplySequenceItem
from app.worker.reply_sequence_dispatch import dispatch_claim
from app.worker.reply_sequence_notifications import notify_once
from jsonschema import Draft202012Validator
from sqlalchemy import select, update

from test_reply_sends_postgres import _command, _seed_reply
from test_reply_sequence_acceptance_postgres import same_account
from test_reply_sequence_dispatch_postgres import (
    FakeTelegram,
    accept,
    inbound,
    queue,
    runtime_fixture,
)
from test_reply_sequence_dispatch_postgres import stable_dispatch_clock as stable_dispatch_clock


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def context(runtime, sessions, seed, **arguments):
    async with sessions() as session:
        result = await mcp_routes._get_reply_context_tool_result(
            request_id="review-regression",
            arguments={"task_id": str(seed.task_id), **arguments},
            principal=_command(seed, seed.draft_ids[0]).principal,
            session=session,
            settings=runtime.settings,
        )
        content = result["result"]["structuredContent"]
        Draft202012Validator(MCP_TOOL_OUTPUT_SCHEMAS["gate_get_reply_context"]).validate(content)
        return content


async def project_inbound(sessions, seed, *, message_id=700, update_id=500):
    """Synthetic consumer projection with real SQL commits; provider is never invoked."""
    identity = uuid4()
    async with sessions() as session:
        session.add(
            Message(
                id=identity,
                bot_id=seed.bot_id,
                account_id=seed.account_id,
                project_id=seed.project_id,
                direction="inbound",
                source="telegram",
                event_type="business_message",
                chat_id=seed.recipient_chat_id,
                sender_id=seed.recipient_chat_id,
                text="NEW CONTEXT",
                telegram_update_id=update_id,
                telegram_message_id=message_id,
                business_connection_id=seed.business_connection_id,
                payload={},
                received_at=datetime.now(UTC),
            )
        )
        await session.flush()
        await session.execute(
            update(ReplyTask)
            .where(ReplyTask.id == seed.task_id)
            .values(trigger_message_id=identity)
        )
        await session.execute(
            update(ReplyDraft)
            .where(ReplyDraft.id.in_(seed.draft_ids))
            .values(trigger_message_id=identity)
        )
        receipt = await session.scalar(
            select(TelegramReceipt).where(TelegramReceipt.update_id == update_id)
        )
        receipt.status, receipt.processed_at = "processed", datetime.now(UTC)
        await session.execute(
            update(TelegramOutboxEvent)
            .where(TelegramOutboxEvent.receipt_id == receipt.id)
            .values(status="delivered", delivered_at=datetime.now(UTC))
        )
        await session.commit()
    return identity


@pytest.mark.anyio
async def test_inbound_between_context_reads_cannot_authorize_old_history(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        q = await queue(runtime, sessions, seed)
        claim = await runtime.claim(q.sequence_id)
        original = mcp_routes.get_reply_context

        async def race(**kwargs):
            old = await original(**kwargs)
            await accept(sessions, inbound(seed))
            return old

        monkeypatch.setattr(mcp_routes, "get_reply_context", race)
        result = await context(runtime, sessions, seed)
        assert result["messages"][0]["text"] == "Initial inbound message"
        assert result["dialog_inbound_revision"] is None
        assert result["sequence_approval"] == {
            "ready": False,
            "blocked_reason": "recipient_context_changed",
        }
        for revision, reason in [
            (0, "recipient_context_changed"),
            (1, "recipient_context_pending"),
        ]:
            with pytest.raises(ReplySequenceError) as exc:
                await queue(runtime, sessions, seed, revision=revision)
            assert exc.value.code == reason
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert not telegram.calls
        async with sessions() as session:
            assert await session.scalar(select(SendAttempt.id)) is None
            assert (await session.get(ReplySequence, q.sequence_id)).state == "stopped"


@pytest.mark.anyio
@pytest.mark.parametrize("receipt_state", ["received", "processing", "dead_letter", "processed"])
async def test_committed_receipt_without_projection_cannot_queue(receipt_state):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await accept(sessions, inbound(seed))
        async with sessions() as session:
            await session.execute(update(TelegramReceipt).values(status=receipt_state))
            await session.commit()
        result = await context(runtime, sessions, seed)
        assert result["dialog_inbound_revision"] is None
        assert result["sequence_approval"]["blocked_reason"] == "recipient_context_pending"
        with pytest.raises(ReplySequenceError) as exc:
            await queue(runtime, sessions, seed, revision=1)
        assert exc.value.code == "recipient_context_pending"
        async with sessions() as session:
            assert await session.scalar(select(ReplySequence.id)) is None
            assert await session.scalar(select(SendAttempt.id)) is None


@pytest.mark.anyio
async def test_projection_then_fresh_visible_context_allows_new_approval():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await accept(sessions, inbound(seed))
        identity = await project_inbound(sessions, seed)
        result = await context(runtime, sessions, seed, limit=1)
        assert result["messages"][0]["id"] == str(identity)
        assert result["sequence_approval"] == {"ready": True, "blocked_reason": None}
        assert result["dialog_inbound_revision"] == 1
        old_page = await context(runtime, sessions, seed, limit=1, cursor=result["next_cursor"])
        assert old_page["dialog_inbound_revision"] is None
        assert old_page["sequence_approval"]["blocked_reason"] == "recipient_context_not_visible"
        q = await queue(runtime, sessions, seed, revision=1)
        claim = await runtime.claim(q.sequence_id)
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert len(telegram.calls) == 1


@pytest.mark.anyio
async def test_earlier_pending_receipt_blocks_even_when_latest_is_projected():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await accept(sessions, inbound(seed))
        await accept(sessions, inbound(seed, update_id=501, message_id=701))
        await project_inbound(sessions, seed, update_id=501, message_id=701)
        result = await context(runtime, sessions, seed)
        assert result["dialog_inbound_revision"] is None
        with pytest.raises(ReplySequenceError) as exc:
            await queue(runtime, sessions, seed, revision=2)
        assert exc.value.code == "recipient_context_pending"


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["old_task", "wrong_project", "wrong_connection"])
async def test_projected_inbound_requires_the_current_scoped_task(fault):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        other = await _seed_reply(sessions, draft_count=2)
        await accept(sessions, inbound(seed))
        identity = await project_inbound(sessions, seed)
        async with sessions() as session:
            if fault == "old_task":
                await session.execute(
                    update(ReplyTask)
                    .where(ReplyTask.id == seed.task_id)
                    .values(trigger_message_id=seed.trigger_message_id)
                )
            else:
                value = (
                    {"project_id": other.project_id}
                    if fault == "wrong_project"
                    else {"business_connection_id": other.business_connection_id}
                )
                await session.execute(update(Message).where(Message.id == identity).values(**value))
                if fault == "wrong_project":
                    await session.execute(
                        update(ReplyTask)
                        .where(ReplyTask.id == seed.task_id)
                        .values(trigger_message_id=seed.trigger_message_id)
                    )
            await session.commit()
        result = await context(runtime, sessions, seed)
        assert result["dialog_inbound_revision"] is None
        with pytest.raises(ReplySequenceError) as exc:
            await queue(runtime, sessions, seed, revision=1)
        assert exc.value.code == (
            "recipient_context_changed" if fault == "old_task" else "recipient_context_pending"
        )


@pytest.mark.anyio
async def test_admission_rechecks_receipt_projection_before_sending():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        await accept(sessions, inbound(seed))
        await project_inbound(sessions, seed)
        q = await queue(runtime, sessions, seed, revision=1)
        claim = await runtime.claim(q.sequence_id)
        async with sessions() as session:
            await session.execute(update(TelegramReceipt).values(status="processing"))
            await session.commit()
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert not telegram.calls
        async with sessions() as session:
            assert await session.scalar(select(SendAttempt.id)) is None


@pytest.mark.anyio
async def test_stopped_detail_returns_all_fifty_saved_bodies_with_scoped_pagination():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=50)
        q = await queue(runtime, sessions, seed)
        await accept(sessions, inbound(seed))
        principal = _command(seed, seed.draft_ids[0]).principal
        bodies, cursor = [], 0
        async with sessions() as session:
            # Old task state does not erase the saved draft bodies.
            await session.execute(
                update(ReplyTask).where(ReplyTask.id == seed.task_id).values(status="closed")
            )
            await session.commit()
            while True:
                detail = await runtime.detail(
                    session, principal, q.sequence_id, cursor=cursor, limit=7
                )
                Draft202012Validator(SEQUENCE_DETAIL_SCHEMA).validate(detail)
                assert detail["counts"]["cancelled"] == 50
                assert all(i["draft_text_matches_approval"] for i in detail["items"])
                bodies.extend(i["draft_text"] for i in detail["items"])
                if detail["next_cursor"] is None:
                    break
                cursor = int(detail["next_cursor"])
            assert bodies == [f"Approved draft {i}" for i in range(1, 51)]
            await session.execute(
                update(ReplyDraft)
                .where(ReplyDraft.id == seed.draft_ids[0])
                .values(draft_text="Edited saved body")
            )
            await session.commit()
            changed = await runtime.detail(session, principal, q.sequence_id, limit=1)
            assert changed["items"][0]["draft_text"] == "Edited saved body"
            assert changed["items"][0]["draft_text_matches_approval"] is False
            for scope in [
                replace(principal.access_scope, account_ids=frozenset({uuid4()})),
                replace(principal.access_scope, project_ids=frozenset({uuid4()})),
                replace(principal.access_scope, workspace_id="foreign"),
            ]:
                assert (
                    await runtime.detail(
                        session, replace(principal, access_scope=scope), q.sequence_id
                    )
                    is None
                )
                if scope.workspace_id == runtime.workspace:
                    assert await approval_context_state(session, scope, seed.task_id) is None


@pytest.mark.anyio
async def test_detail_does_not_expose_a_foreign_draft_linked_by_corrupt_item():
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        other = await _seed_reply(sessions, draft_count=2, draft_text="FOREIGN BODY")
        q = await queue(runtime, sessions, seed)
        principal = _command(seed, seed.draft_ids[0]).principal
        async with sessions() as session:
            await session.execute(
                update(ReplySequenceItem)
                .where(
                    ReplySequenceItem.sequence_id == q.sequence_id, ReplySequenceItem.position == 0
                )
                .values(draft_id=other.draft_ids[0])
            )
            await session.commit()
            detail = await runtime.detail(session, principal, q.sequence_id)
            assert detail["items"][0]["draft_text"] is None
            assert detail["items"][0]["draft_text_matches_approval"] is None
            assert "FOREIGN BODY" not in str(detail)


@pytest.mark.anyio
@pytest.mark.parametrize("named", [True, False])
async def test_two_stops_same_account_have_distinct_scoped_recipient_labels(named):
    async with runtime_fixture() as (runtime, sessions):
        a = await _seed_reply(sessions, draft_count=2)
        b = await same_account(sessions, a, await _seed_reply(sessions, draft_count=2))
        async with sessions() as session:
            account = await session.get(Account, a.account_id)
            account.permissions = {"reply_sequence_owner_subject": "reply-send-postgres-test"}
            if named:
                for seed, title in [(a, "Получатель А\n\u202e"), (b, "Получатель Б")]:
                    await session.execute(
                        update(Conversation)
                        .where(Conversation.id == seed.conversation_id)
                        .values(title=title)
                    )
            await session.commit()
        qa, qb = await queue(runtime, sessions, a), await queue(runtime, sessions, b)
        await accept(sessions, inbound(a, content={"text": "PRIVATE INBOUND A"}))
        await accept(sessions, inbound(b, update_id=501, content={"text": "PRIVATE INBOUND B"}))
        telegram = FakeTelegram()
        assert await notify_once(runtime, telegram)
        assert await notify_once(runtime, telegram)
        principal = _command(a, a.draft_ids[0]).principal
        principal = replace(
            principal,
            access_scope=AccessScope(
                workspace_id=runtime.workspace,
                account_ids=frozenset({a.account_id}),
                project_ids=frozenset({a.project_id}),
            ),
        )
        async with sessions() as session:
            labels = [
                (await runtime.detail(session, principal, q.sequence_id))["recipient_display"]
                for q in (qa, qb)
            ]
        assert labels[0] != labels[1]
        if named:
            assert labels == ["Получатель А", "Получатель Б"]
        else:
            assert all(label.startswith("Адресат без имени · диалог ") for label in labels)
        assert len(telegram.calls) == 2
        assert all(call["chat_id"] == "999" for call in telegram.calls)
        texts = [call["text"] for call in telegram.calls]
        assert all(sum(label in text for text in texts) == 1 for label in labels)
        assert all("PRIVATE INBOUND" not in text and "\u202e" not in text for text in texts)
