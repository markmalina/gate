"""Sprint 2 actual MCP handlers + PostgreSQL, with synthetic envelopes and no provider I/O."""

import base64
import hashlib
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.api import mcp_routes
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.core.config import get_settings
from app.main import create_app
from app.models.domain import AuditLog, ReplyDraft, TelegramReceipt
from jsonschema import Draft202012Validator
from sqlalchemy import select, update

from test_dialog_snapshots_postgres import receive
from test_reply_media_assets import media_input
from test_reply_media_transport import source
from test_reply_sends_postgres import (
    _command,
    _LiteralRecordingTelegram,
    _postgres_sessions,
    _seed_reply,
    _settings,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def connected(monkeypatch, sessions, seed, *, read=True):
    principal = replace(
        _command(seed, seed.draft_ids[0]).principal,
        scopes=frozenset(
            {"gate:drafts:write", "gate:messages:send"} | ({"gate:inbox:read"} if read else set())
        ),
    )

    async def authenticate(**kwargs):
        assert kwargs["required_scope"] in principal.scopes
        return principal

    async def database():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(mcp_routes, "_require_mcp_principal", authenticate)
    settings = _settings().model_copy(
        update={"gate_mcp_enabled": True, "gate_document_send_enabled": True}
    )
    app = create_app()
    app.dependency_overrides[get_db_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    telegram = _LiteralRecordingTelegram()
    app.dependency_overrides[get_telegram_client] = lambda: telegram
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://gate.example",
    ) as client:

        async def call(name, args):
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            schema = mcp_routes.MCP_TOOL_OUTPUT_SCHEMAS.get(name)
            if schema:
                Draft202012Validator(schema).validate(result["structuredContent"])
            return result["structuredContent"]

        yield call, telegram, principal


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["photo", "album", "rich_photo", "rich_slideshow", "rich_collage"])
async def test_actual_media_read_prepare_preview_stale_and_new_cycle(monkeypatch, mode):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with connected(monkeypatch, sessions, seed) as (call, telegram, _):
            read = await call("gate_get_reply_context", {"task_id": str(seed.task_id)})
            assert read["context_ready"]
            item = media_input()
            count = 2 if mode in {"album", "rich_slideshow", "rich_collage"} else 1
            args = {
                "task_id": str(seed.task_id),
                "expected_context_snapshot": read["context_snapshot"],
                "mode": mode,
                "items": [asdict(item)] * count,
                "staging_key": "original",
                "execution_id": "s2-synthetic",
                "execution_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
            if mode.startswith("rich_"):
                args["rich_content"] = source(
                    mode, [(f"item:{i}", item.expected_sha256) for i in range(1, count + 1)]
                )
            missing = {k: v for k, v in args.items() if k != "expected_context_snapshot"}
            assert (await call("gate_prepare_reply_media", missing))[
                "error"
            ] == "context_snapshot_required"
            prepared = await call("gate_prepare_reply_media", args)
            assert prepared["snapshot"]["context_snapshot"] == read["context_snapshot"]
            preview = await call("gate_preview_reply_media", {"package_id": prepared["package_id"]})
            assert preview == prepared
            await receive(sessions, seed, content={"voice": {}}, update_id=101)
            refused = await call(
                "gate_send_approved_reply",
                {
                    "draft_id": prepared["draft_id"],
                    "confirmed": True,
                    **prepared["confirmation"],
                    "execution_id": "s2-synthetic",
                },
            )
            assert refused["error"] == "context_changed" and refused["sent"] is False
            assert refused["current_context"]["context_snapshot"] != read["context_snapshot"]
            stale_prepare = await call("gate_prepare_reply_media", args)
            assert stale_prepare["error"] == "context_changed"
            fresh_args = {
                **args,
                "expected_context_snapshot": refused["current_context"]["context_snapshot"],
            }
            assert (await call("gate_prepare_reply_media", fresh_args))[
                "error"
            ] == "media_staging_conflict"
            renewed = await call(
                "gate_prepare_reply_media", {**fresh_args, "staging_key": "new-review"}
            )
            assert renewed["draft_id"] != prepared["draft_id"]
            assert renewed["package_sha256"] != prepared["package_sha256"]
            assert telegram.plain_calls == []


@pytest.mark.anyio
async def test_raw_pages_truncation_history_and_explicit_gap_never_mint_false_ready(monkeypatch):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        for identifier in range(1, 24):
            await receive(sessions, seed, update_id=identifier)
        async with connected(monkeypatch, sessions, seed) as (call, telegram, _):
            first = await call("gate_get_reply_context", {"task_id": str(seed.task_id)})
            assert first["context_snapshot"] is None
            assert first["context_reason"] == "raw_readset_incomplete"
            leaked_fingerprint = __import__("json").loads(
                base64.urlsafe_b64decode(first["dialog_events_next_cursor"])
            )["snapshot"]
            unsafe = await call(
                "gate_save_reply_draft",
                {
                    "task_id": str(seed.task_id),
                    "text": "Must not prepare",
                    "expected_context_snapshot": leaked_fingerprint,
                },
            )
            assert unsafe["error"] == "context_unavailable" and unsafe["sent"] is False
            page = await call(
                "gate_get_reply_context",
                {
                    "task_id": str(seed.task_id),
                    "dialog_events_cursor": first["dialog_events_next_cursor"],
                },
            )
            assert len(page["dialog_events"]) == 3 and page["context_snapshot"] is None
            assert page["context_reason"] == "historical_page"
            # Projected history >20 is not an eternal veto. The remaining raw
            # view is historical; actual projected chronology is read separately.
            async with sessions() as session:
                await session.execute(update(TelegramReceipt).values(status="processed"))
                await session.commit()
            ready = await call("gate_get_reply_context", {"task_id": str(seed.task_id)})
            assert ready["context_ready"]
            await receive(sessions, seed, update_id=30, content={"text": "x" * 5000})
            long_read = await call("gate_get_reply_context", {"task_id": str(seed.task_id)})
            assert long_read["context_reason"] == "raw_text_truncated"
            event = next(e for e in long_read["dialog_events"] if e["text_truncated"])
            rest = await call(
                "gate_get_reply_context",
                {
                    "task_id": str(seed.task_id),
                    "dialog_events_cursor": event["text_next_cursor"],
                },
            )
            assert len(rest["dialog_events"][0]["text"]) == 904
            assert rest["context_snapshot"] is None
            # A cursor is a scoped history selector, never delivery authority.
            forged = await call(
                "gate_get_reply_context",
                {
                    "task_id": str(seed.task_id),
                    "dialog_events_cursor": "not-a-cursor",
                },
            )
            assert forged["error"] == "invalid_arguments"
            async with sessions() as session:
                session.add(
                    AuditLog(
                        actor="synthetic-gap-producer",
                        action="reply_dialog_ingress_gap",
                        account_id=seed.account_id,
                        metadata_={"chat_id": seed.recipient_chat_id},
                    )
                )
                await session.commit()
            gap = await call("gate_get_reply_context", {"task_id": str(seed.task_id)})
            assert gap["context_reason"] == "known_ingress_gap" and not gap["context_ready"]
            stopped = await call(
                "gate_send_approved_reply",
                {
                    "draft_id": str(seed.draft_ids[0]),
                    "confirmed": True,
                    "expected_account": "RU",
                    "expected_recipient_chat_id": seed.recipient_chat_id,
                },
            )
            assert stopped["error"] == "context_unavailable" and stopped["sent"] is False
            assert telegram.plain_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["text", "document"])
async def test_actual_prepare_stale_missing_foreign_and_fresh_binding(monkeypatch, kind):
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        async with connected(monkeypatch, sessions, seed) as (call, telegram, _):
            read = await call("gate_get_reply_context", {"task_id": str(seed.task_id)})
            name = "gate_save_reply_draft" if kind == "text" else "gate_prepare_reply_document"
            if kind == "text":
                args = {
                    "task_id": str(seed.task_id),
                    "text": "Exact approved synthetic",
                    "text_format": "plain",
                }
            else:
                data = b"%PDF-1.7\nsynthetic fixture"
                args = {
                    "task_id": str(seed.task_id),
                    "filename": "synthetic.pdf",
                    "media_type": "application/pdf",
                    "content_base64": base64.b64encode(data).decode(),
                    "expected_sha256": hashlib.sha256(data).hexdigest(),
                }
            missing = await call(name, args)
            assert missing["error"] == "context_snapshot_required" and missing["sent"] is False
            foreign = await call(
                name, {**args, "expected_context_snapshot": "dialog-v1:" + "a" * 64}
            )
            assert foreign["error"] == "context_changed"
            saved = await call(
                name, {**args, "expected_context_snapshot": read["context_snapshot"]}
            )
            async with sessions() as session:
                draft = await session.get(ReplyDraft, saved["draft_id"])
                assert draft.prompt_context["dialog_snapshot"] == read["context_snapshot"]
            await receive(sessions, seed)
            refused = await call(
                name, {**args, "expected_context_snapshot": read["context_snapshot"]}
            )
            assert refused["error"] == "context_changed" and refused["sent"] is False
            fresh = await call(
                name,
                {
                    **args,
                    "expected_context_snapshot": refused["current_context"]["context_snapshot"],
                },
            )
            assert fresh["draft_id"] != saved["draft_id"]
            assert telegram.plain_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["sent", "failed", "unknown"])
async def test_legacy_completed_media_receipt_replays_without_snapshot_or_budget_charge(outcome):
    """Seed a historical media row before any new send; do not backfill its approval."""
    from copy import deepcopy
    from uuid import uuid4

    from app.application.reply_media import media_package_digest
    from app.infrastructure.reply_sends import SqlTelegramReplySender, _empty_media_receipt
    from app.models.domain import SendAttempt
    from app.models.reply_media import ReplyMediaExecutionBudget, ReplyMediaPackage

    from test_reply_media_transport import MediaTelegram, deliver, prepared

    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions)
        _, command = await prepared(sessions, seed)
        attempt_id = uuid4()
        async with sessions() as session:
            draft = await session.get(ReplyDraft, command.draft_id)
            package = await session.scalar(select(ReplyMediaPackage))
            snapshot = deepcopy(package.snapshot_json)
            snapshot.pop("context_snapshot")
            digest = media_package_digest(snapshot)
            snapshot["package_sha256"] = digest
            package.snapshot_json, package.package_sha256 = snapshot, digest
            context = {**draft.prompt_context}
            context.pop("dialog_snapshot")
            context.pop("dialog_snapshot_workspace")
            context["_gate_media_package"] = {
                **context["_gate_media_package"],
                "package_sha256": digest,
            }
            draft.prompt_context = context
            command = replace(command, expected_media_package_sha256=digest)
            await session.flush()
            sender = SqlTelegramReplySender(
                session=session, telegram=MediaTelegram(), settings=_settings()
            )
            saved_context = await sender._load_context_snapshot(command, for_update=False)
            receipt = _empty_media_receipt(saved_context.media, outcome)
            receipt["provider_message_ids"] = ["742"] if outcome == "sent" else []
            package.receipt_json = receipt
            session.add(
                SendAttempt(
                    id=attempt_id,
                    reply_draft_id=draft.id,
                    reply_task_id=seed.task_id,
                    conversation_id=seed.conversation_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    principal_subject=command.principal.subject,
                    status=outcome,
                    account_label="RU",
                    target_chat_id=seed.recipient_chat_id,
                    business_connection_id=seed.business_connection_id,
                    provider_message_id="742" if outcome == "sent" else None,
                )
            )
            session.add(
                AuditLog(
                    actor="synthetic-historical-fixture",
                    action="reply_send_reserved_from_mcp",
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    metadata_={"attempt_id": str(attempt_id), "payload_sha256": digest},
                )
            )
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            budget.writes_reserved, budget.messages_reserved = 1, 1
            await session.commit()
        await receive(sessions, seed)
        telegram = MediaTelegram()
        result = await deliver(sessions, telegram, command)
        assert result.idempotent_replay and result.status == outcome and result.media == receipt
        assert result.sent == (outcome == "sent")
        assert telegram.calls == []
        async with sessions() as session:
            budget = await session.scalar(select(ReplyMediaExecutionBudget))
            assert (budget.writes_reserved, budget.messages_reserved) == (1, 1)
            draft = await session.get(ReplyDraft, command.draft_id)
            assert "dialog_snapshot" not in draft.prompt_context
