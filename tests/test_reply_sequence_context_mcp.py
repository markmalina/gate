"""MCP sequence refusals expose scoped context without approving stale drafts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api import mcp_routes
from app.api.mcp_schemas import MCP_TOOL_OUTPUT_SCHEMAS
from app.application.reply_sequences import ReplySequenceError
from app.core.mcp_security import AccessScope, Principal
from app.models.domain import AuditLog, ReplyDraft, SendAttempt
from app.worker.reply_sequences import run_reply_sequence_once
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from test_dialog_snapshots_postgres import receive
from test_reply_sends_postgres import (
    _command,
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _seed_reply,
    _settings,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _principal(*, read: bool) -> Principal:
    return Principal(
        subject="synthetic-sequence-context", auth_method="test",
        scopes=frozenset({"gate:messages:send", "gate:drafts:write"}
                         | ({"gate:inbox:read"} if read else set())),
        access_scope=AccessScope(
            workspace_id="synthetic", account_ids=frozenset({uuid4()}),
            project_ids=frozenset({uuid4()}),
        ),
    )


def _arguments(draft_ids) -> dict[str, Any]:
    return {
        "draft_ids": [str(value) for value in draft_ids], "confirmed": True,
        "expected_account": "RU", "expected_recipient_chat_id": "12345",
        "interval_seconds": 60,
    }


def _content(response):
    content = response["result"]["structuredContent"]
    Draft202012Validator(
        MCP_TOOL_OUTPUT_SCHEMAS["gate_queue_approved_reply_sequence"],
    ).validate(content)
    return content


def _reject_writer(monkeypatch, code: str) -> None:
    class RejectingWriter:
        def __init__(self, session):
            self.session = session

        async def queue(self, command):
            raise ReplySequenceError(code=code, message="Synthetic refusal")

    monkeypatch.setattr(mcp_routes, "SqlReplySequenceWriter", RejectingWriter)


@pytest.mark.anyio
@pytest.mark.parametrize("code", [
    "context_changed", "context_snapshot_required", "context_unavailable",
])
@pytest.mark.parametrize("read", [False, True])
async def test_sequence_freshness_errors_share_scoped_context_contract(
    monkeypatch, code: str, read: bool,
) -> None:
    _reject_writer(monkeypatch, code)
    task_id = uuid4()
    session = AsyncMock()
    session.scalar.return_value = task_id
    context = {"context_snapshot": "dialog-v1:" + "a" * 64, "context_ready": True}
    reader = AsyncMock(return_value=context)
    monkeypatch.setattr(mcp_routes, "get_reply_context", reader)
    response = await mcp_routes._queue_approved_reply_sequence_tool_result(
        request_id=1, arguments=_arguments((uuid4(), uuid4())),
        principal=_principal(read=read), session=session,
    )
    assert response["result"]["isError"] is True
    assert _content(response) == {
        "error": code, "sent": False, "current_context": context if read else None,
    }
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
    if read:
        assert reader.await_args.kwargs["task_id"] == task_id
    else:
        session.scalar.assert_not_awaited()
        reader.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["outside_scope", "context_read_failed"])
async def test_sequence_context_lookup_failure_preserves_original_refusal(
    monkeypatch, failure: str,
) -> None:
    _reject_writer(monkeypatch, "context_changed")
    session = AsyncMock()
    session.scalar.return_value = None if failure == "outside_scope" else uuid4()
    reader = AsyncMock(side_effect=RuntimeError("synthetic private DB detail"))
    monkeypatch.setattr(mcp_routes, "get_reply_context", reader)
    response = await mcp_routes._queue_approved_reply_sequence_tool_result(
        request_id=1, arguments=_arguments((uuid4(), uuid4())),
        principal=_principal(read=True), session=session,
    )
    assert _content(response) == {
        "error": "context_changed", "sent": False, "current_context": None,
    }
    assert "synthetic private DB detail" not in str(response)
    if failure == "outside_scope":
        reader.assert_not_awaited()
    else:
        reader.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("code", ["draft_not_found", "sequence_already_queued"])
async def test_nonfreshness_sequence_errors_do_not_read_context(monkeypatch, code: str) -> None:
    _reject_writer(monkeypatch, code)
    session = AsyncMock()
    reader = AsyncMock()
    monkeypatch.setattr(mcp_routes, "get_reply_context", reader)
    response = await mcp_routes._queue_approved_reply_sequence_tool_result(
        request_id=1, arguments=_arguments((uuid4(), uuid4())),
        principal=_principal(read=True), session=session,
    )
    assert _content(response) == {"error": code}
    reader.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("read", [False, True])
async def test_real_sequence_refusal_preserves_old_drafts_and_scope(
    owner: bool, read: bool,
) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await receive(sessions, seed, owner=owner)
        principal = _command(seed, seed.draft_ids[0]).principal
        if read:
            principal = replace(principal, scopes=principal.scopes | {"gate:inbox:read"})
        async with sessions() as session:
            response = await mcp_routes._queue_approved_reply_sequence_tool_result(
                request_id=1, arguments=_arguments(seed.draft_ids),
                principal=principal, session=session,
            )
        content = _content(response)
        assert content["error"] == "context_changed" and content["sent"] is False
        if read:
            current = content["current_context"]
            assert current["context_snapshot"] != seed.context_snapshot
            assert current["dialog_events"][-1]["direction"] == (
                "outbound" if owner else "inbound"
            )
        else:
            assert content["current_context"] is None
        async with sessions() as session:
            drafts = (await session.scalars(select(ReplyDraft).where(
                ReplyDraft.id.in_(seed.draft_ids),
            ))).all()
            assert all(d.prompt_context["dialog_snapshot"] == seed.context_snapshot for d in drafts)
            assert all("reply_sequence" not in d.prompt_context for d in drafts)
            assert await session.scalar(select(func.count()).select_from(SendAttempt)) == 0
            assert await session.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "reply_sequence_queued_from_mcp",
            )) == 0


@pytest.mark.anyio
@pytest.mark.parametrize("read", [False, True])
async def test_real_legacy_sequence_returns_snapshot_required_without_backfill(read: bool) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        async with sessions() as session:
            for draft_id in seed.draft_ids:
                draft = await session.get(ReplyDraft, draft_id)
                draft.prompt_context = {}
            await session.commit()
        principal = _command(seed, seed.draft_ids[0]).principal
        if read:
            principal = replace(principal, scopes=principal.scopes | {"gate:inbox:read"})
        async with sessions() as session:
            response = await mcp_routes._queue_approved_reply_sequence_tool_result(
                request_id=1, arguments=_arguments(seed.draft_ids),
                principal=principal, session=session,
            )
        content = _content(response)
        assert content["error"] == "context_snapshot_required" and content["sent"] is False
        assert (content["current_context"] is not None) is read
        async with sessions() as session:
            for draft_id in seed.draft_ids:
                assert (await session.get(ReplyDraft, draft_id)).prompt_context == {}


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["account", "project"])
async def test_sequence_refusal_never_exposes_foreign_scope_context(boundary: str) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await receive(sessions, seed)
        principal = _command(seed, seed.draft_ids[0]).principal
        scope = principal.access_scope
        scope = replace(scope, **{
            "account_ids" if boundary == "account" else "project_ids": frozenset({uuid4()}),
        })
        principal = replace(
            principal, scopes=principal.scopes | {"gate:inbox:read"}, access_scope=scope,
        )
        async with sessions() as session:
            response = await mcp_routes._queue_approved_reply_sequence_tool_result(
                request_id=1, arguments=_arguments(seed.draft_ids),
                principal=principal, session=session,
            )
        assert _content(response) == {"error": "draft_not_found"}


@pytest.mark.anyio
@pytest.mark.parametrize("owner", [False, True])
async def test_refusal_context_can_prepare_new_exact_drafts_and_queue(owner: bool) -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        await receive(sessions, seed, owner=owner)
        principal = _command(seed, seed.draft_ids[0]).principal
        principal = replace(principal, scopes=principal.scopes | {
            "gate:inbox:read", "gate:drafts:write",
        })
        async with sessions() as session:
            refused = await mcp_routes._queue_approved_reply_sequence_tool_result(
                request_id=1, arguments=_arguments(seed.draft_ids),
                principal=principal, session=session,
            )
        snapshot = _content(refused)["current_context"]["context_snapshot"]
        assert snapshot != seed.context_snapshot
        drafts, hashes = [], []
        for index in range(2):
            async with sessions() as session:
                response = await mcp_routes._save_reply_draft_tool_result(
                    request_id=index + 2, arguments={
                        "task_id": str(seed.task_id), "expected_context_snapshot": snapshot,
                        "text": f"New exact synthetic reply {index}", "text_format": "plain",
                    }, principal=principal, session=session,
                )
            assert response["result"]["isError"] is False
            saved = response["result"]["structuredContent"]
            drafts.append(saved["draft_id"])
            hashes.append(saved["formatting"]["payload_sha256"])
        arguments = {**_arguments(drafts), "expected_payload_sha256s": hashes}
        async with sessions() as session:
            queued = await mcp_routes._queue_approved_reply_sequence_tool_result(
                request_id=4, arguments=arguments, principal=principal, session=session,
            )
        content = _content(queued)
        assert content["queued"] is True and content["sent"] is False
        assert [item["draft_id"] for item in content["items"]] == drafts
        telegram = _LiteralRecordingTelegram()
        assert await run_reply_sequence_once(
            settings=_settings(), session_factory=sessions, telegram=telegram,
            now=datetime.now(UTC) + timedelta(minutes=1),
        ) == 1
        assert len(telegram.plain_calls) == 1
        assert telegram.plain_calls[0]["text"] == "New exact synthetic reply 0"


@pytest.mark.anyio
async def test_sequence_refusal_exposes_raw_event_truncation() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2)
        for update_id in range(1, 23):
            await receive(sessions, seed, update_id=update_id)
        principal = _command(seed, seed.draft_ids[0]).principal
        principal = replace(principal, scopes=principal.scopes | {"gate:inbox:read"})
        async with sessions() as session:
            response = await mcp_routes._queue_approved_reply_sequence_tool_result(
                request_id=1, arguments=_arguments(seed.draft_ids),
                principal=principal, session=session,
            )
        content = _content(response)
        assert content["error"] == "context_changed"
        current = content["current_context"]
        assert current["dialog_events_has_more"] is True
        assert len(current["dialog_events"]) == 20


def test_catalogue_requires_reviewed_snapshot_and_describes_refusal() -> None:
    settings = _settings()
    for tool in (
        mcp_routes._gate_save_reply_draft_tool(settings),
        mcp_routes._gate_prepare_reply_document_tool(settings),
    ):
        assert "expected_context_snapshot" in tool["inputSchema"]["required"]
    queue = mcp_routes._gate_queue_approved_reply_sequence_tool(settings)
    assert "current_context" in queue["description"]
    assert queue["inputSchema"]["properties"]["interval_seconds"]["const"] == 60
    assert queue["inputSchema"]["properties"]["draft_ids"]["maxItems"] == 50
