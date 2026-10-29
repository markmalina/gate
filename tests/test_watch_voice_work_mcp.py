"""Actual MCP route, principal containment and immutable Watch Work submission."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from app.api import mcp_metadata_routes, mcp_routes
from app.api import mcp_watch_voice_work as adapter
from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.core.config import get_settings
from app.core.mcp_oauth import McpOAuthError
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.watch_voice_github import WatchVoiceError, body_digest
from app.main import app
from app.models.domain import UNCLASSIFIED_PROJECT_ID
from app.models.watch_voice import WatchVoiceNote, WatchVoiceTextReceipt, WatchVoiceWorkClaim
from app.worker import watch_voice as worker
from jsonschema import Draft202012Validator, validate
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_watch_voice import database
from tests.test_watch_voice_work import KEY, REV, SHA, TOKEN, WorkWorld, cfg, client, current, seed


def configuration(**changes):
    return cfg().model_copy(update={
        "gate_mcp_enabled": True, "mcp_auth_mode": "oauth",
        "mcp_authorization_server": "https://auth.synthetic.example",
        "mcp_public_resource_url": "https://gate.synthetic.example/mcp",
        "mcp_oauth_jwks_url": "https://auth.synthetic.example/jwks",
        "gate_watch_voice_work_mcp_enabled": True,
        "gate_watch_voice_work_owner_subject": "synthetic-owner-subject",
        **changes,
    })


def principal(**changes):
    return replace(Principal(
        subject="synthetic-owner-subject", auth_method="oauth2",
        scopes=frozenset({adapter.SCOPE}),
        access_scope=AccessScope("mark-personal", frozenset({UUID(int=2)}),
                                 frozenset({UNCLASSIFIED_PROJECT_ID})),
    ), **changes)


def arguments(**changes):
    return {"event_id": KEY, "token": TOKEN, "revision": REV, "audio_sha256": SHA, **changes}


async def endpoint(session, settings, name, args):
    return await mcp_routes.mcp_endpoint(
        payload={"jsonrpc": "2.0", "id": "synthetic", "method": "tools/call",
                 "params": {"name": name, "arguments": args}},
        authorization="Bearer synthetic", settings=settings, session=session, telegram=None,
    )


def test_actual_http_route_claim_start_submit_and_replays(tmp_path, monkeypatch):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            settings = configuration()
            auth = AsyncMock(return_value=principal())
            monkeypatch.setattr(mcp_routes, "_require_mcp_principal", auth)

            async def session_dependency():
                async with factory() as session:
                    yield session

            previous = app.dependency_overrides.copy()
            app.dependency_overrides.update({get_settings: lambda: settings,
                                            get_db_session: session_dependency,
                                            get_telegram_client: lambda: None})
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="https://synthetic") as http:
                    listed = (await http.post("/mcp", json={
                        "jsonrpc": "2.0", "id": 1, "method": "tools/list",
                    })).json()["result"]["tools"]
                    assert set(adapter.TOOL_NAMES) <= {t["name"] for t in listed}

                    async def call(name, args):
                        response = await http.post("/mcp", json={
                            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": name, "arguments": args},
                        })
                        assert response.status_code == 200
                        result = response.json()["result"]
                        validate(result["structuredContent"], adapter.OUTPUT_SCHEMA)
                        return result

                    first = await call(adapter.TOOL_NAMES[0], arguments())
                    again = await call(adapter.TOOL_NAMES[0], arguments())
                    assert first == again and not first["isError"]
                    assert first["structuredContent"]["state"] == "claimed"
                    start = {"event_id": KEY, "token": TOKEN}
                    started = (await call(adapter.TOOL_NAMES[1], start))["structuredContent"]
                    replay = (await call(adapter.TOOL_NAMES[1], start))["structuredContent"]
                    assert started["start_recorded_now"] and not replay["start_recorded_now"]
                    text = "Полный синтетический текст.\nВторая строка 🥣"
                    submission = arguments(text=text, transcript_sha256=body_digest(text))
                    saved = await call(adapter.TOOL_NAMES[2], submission)
                    assert saved == await call(adapter.TOOL_NAMES[2], submission)
                    receipt = saved["structuredContent"]
                    assert receipt["state"] == "prepared"
                    assert receipt["saved_at"] is receipt["accepted_at"] is None
                    assert receipt["provider_authority"] is False
                    assert text not in json.dumps(saved) and TOKEN not in json.dumps(saved)
                    changed = arguments(text="Подмена", transcript_sha256=body_digest("Подмена"))
                    assert (await call(adapter.TOOL_NAMES[2], changed))["isError"]
                    foreign = await call(adapter.TOOL_NAMES[0], arguments(token="b" * 40))
                    assert foreign["isError"]
                    note, result = await current(factory)
                    assert note.transcript == result.transcript == text
                    assert not note.provider_request_count
                    async with factory() as session:
                        claim = await session.get(WatchVoiceWorkClaim, receipt["claim_id"])
                        assert claim.token_sha256 == body_digest(TOKEN)
                        assert claim.actor_sha256 != body_digest("synthetic-owner-subject")
                    world = WorkWorld()
                    async with httpx.AsyncClient(
                        transport=httpx.MockTransport(world.handle)
                    ) as mark_http:
                        await worker.publish_note(factory, client(mark_http), note)
                    accepted = (await call(adapter.TOOL_NAMES[2], submission))["structuredContent"]
                    assert accepted["state"] == "accepted" and accepted["accepted_at"]
                    assert world.posts == 1 and world.provider_calls == 0
                    assert len(world.comments) == 1
            finally:
                app.dependency_overrides.clear()
                app.dependency_overrides.update(previous)
            assert all(c.kwargs["required_scope"] == adapter.SCOPE for c in auth.call_args_list)

    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"subject": "another-subject"}, {"auth_method": "bearer"}, {"scopes": frozenset()},
    {"access_scope": AccessScope("foreign", frozenset({UUID(int=2)}),
                                 frozenset({UNCLASSIFIED_PROJECT_ID}))},
    {"access_scope": AccessScope("mark-personal", frozenset({UUID(int=2)}),
                                 frozenset({UUID(int=3)}))},
])
def test_denied_principals_never_touch_database(change):
    async def run():
        with pytest.raises(WatchVoiceError, match="scope_denied"):
            await adapter.call_tool(adapter.TOOL_NAMES[0], arguments(), principal(**change),
                                    None, configuration())
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"gate_watch_voice_work_mcp_enabled": False}, {"gate_watch_voice_work_owner_subject": ""},
    {"gate_watch_voice_work_reserve_enabled": False},
    {"gate_watch_voice_early_inbox_enabled": False},
    {"gate_watch_voice_data_gate_approved": False}, {"mcp_auth_mode": "temporary_bearer"},
    {"gate_watch_voice_workspace_key": "foreign"}, {"dropbox_call_account_id": ""},
])
def test_disabled_surface_cannot_be_called_even_by_name(change):
    settings = configuration(**change)
    assert adapter.tool_definitions(settings) == []

    async def run():
        with pytest.raises(WatchVoiceError, match="mcp_disabled"):
            await adapter.call_tool(adapter.TOOL_NAMES[0], arguments(), principal(), None, settings)
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"actor": "forged"}, {"workspace": "foreign"}, {"repository": "foreign/repo"},
    {"provider": "foreign"}, {"url": "https://foreign.invalid"}, {"epoch": 2},
    {"event_id": "latest"}, {"event_id": [KEY]}, {"token": 123},
    {"token": "a" * 31}, {"revision": " "}, {"audio_sha256": "bad"},
])
def test_strict_schema_rejects_spoofing_without_database(change):
    async def run():
        with pytest.raises(WatchVoiceError, match="arguments_invalid"):
            await adapter.call_tool(adapter.TOOL_NAMES[0], arguments(**change), principal(),
                                    None, configuration())
    asyncio.run(run())


def test_discovery_matches_metadata_and_strict_runtime():
    settings = configuration()
    tools = adapter.tool_definitions(settings)
    metadata = mcp_metadata_routes._mcp_metadata(settings=settings)
    assert set(adapter.TOOL_NAMES) <= set(metadata["write_tools"])
    assert not set(adapter.TOOL_NAMES) & set(metadata["read_only_tools"])
    for descriptor in tools:
        Draft202012Validator.check_schema(descriptor["inputSchema"])
        Draft202012Validator.check_schema(descriptor["outputSchema"])
        assert descriptor["inputSchema"]["additionalProperties"] is False
        assert descriptor["annotations"]["readOnlyHint"] is False
        assert descriptor["securitySchemes"] == [{"type": "oauth2", "scopes": [adapter.SCOPE]}]
    validate(arguments(), tools[0]["inputSchema"])


def test_oauth_challenge_precedes_mutation(monkeypatch):
    auth = AsyncMock(side_effect=McpOAuthError(error="insufficient_scope", description="Denied"))
    monkeypatch.setattr(mcp_routes, "_require_mcp_principal", auth)
    response = asyncio.run(endpoint(None, configuration(), adapter.TOOL_NAMES[0], arguments()))
    assert response["result"]["isError"]
    assert "mcp/www_authenticate" in response["result"]["_meta"]


def test_foreign_revision_and_hash_do_not_create_claim(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            async with factory() as session:
                for values in (arguments(revision="another"), arguments(audio_sha256="0" * 64)):
                    with pytest.raises(WatchVoiceError, match="source_conflict"):
                        await adapter.call_tool(adapter.TOOL_NAMES[0], values, principal(),
                                                session, configuration())
            assert (await current(factory))[1] is None
    asyncio.run(run())


def test_caller_pending_state_is_not_flushed_committed_or_rolled_back(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            async with factory() as session:
                pending = WatchVoiceWorkClaim(id="not-persisted", note_id=KEY, epoch=999,
                                             actor_sha256="x", token_sha256="y", state="claimed")
                session.add(pending)
                receipt = await adapter.call_tool(adapter.TOOL_NAMES[0], arguments(), principal(),
                                                  session, configuration())
                assert pending in session.new and receipt["state"] == "claimed"
                async with factory() as other:
                    assert await other.get(WatchVoiceWorkClaim, "not-persisted") is None
    asyncio.run(run())


async def _prepare_adapter_text(factory, text):
    submission = arguments(text=text, transcript_sha256=body_digest(text))
    async with factory() as session:
        await adapter.call_tool(adapter.TOOL_NAMES[0], arguments(), principal(),
                                session, configuration())
        await adapter.call_tool(adapter.TOOL_NAMES[1], {"event_id": KEY, "token": TOKEN},
                                principal(), session, configuration())
        await adapter.call_tool(adapter.TOOL_NAMES[2], submission, principal(),
                                session, configuration())
    return submission


async def _replay_adapter(factory, operation, submission):
    name, values = ((adapter.TOOL_NAMES[0], arguments()) if operation == "claim"
                    else (adapter.TOOL_NAMES[2], submission))
    async with factory() as session:
        return await adapter.call_tool(name, values, principal(), session, configuration())


@pytest.mark.parametrize("phase", ["saved", "accepted"])
@pytest.mark.parametrize("operation", ["claim", "submit"])
@pytest.mark.parametrize("damage", [
    "delete", "body_digest", "issue", "missing_identity",
    "next_part", "delivery_state", "delivered_at",
])
def test_published_replay_rejects_damaged_ledger_or_note_progress(
    tmp_path, phase, operation, damage,
):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            submission = await _prepare_adapter_text(factory, "Синтетический полный текст 🥣")
            world = WorkWorld()
            world.hide_comments = phase == "saved"
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row)
            intact = await _replay_adapter(factory, operation, submission)
            assert intact["state"] == phase
            original_accepted_at = (await current(factory))[1].accepted_at
            async with factory() as session:
                if damage == "delete":
                    await session.execute(delete(WatchVoiceTextReceipt).where(
                        WatchVoiceTextReceipt.note_id == KEY))
                elif damage in {"body_digest", "issue", "missing_identity"}:
                    changes = {"body_digest": {"body_sha256": "0" * 64},
                               "issue": {"issue_number": 999},
                               "missing_identity": {"comment_id": None}}[damage]
                    await session.execute(update(WatchVoiceTextReceipt).where(
                        WatchVoiceTextReceipt.note_id == KEY).values(**changes))
                else:
                    changes = {"next_part": {"next_part": 0},
                               "delivery_state": {"delivery_state": "pending"},
                               "delivered_at": {"delivered_at": None}}[damage]
                    await session.execute(update(WatchVoiceNote).where(
                        WatchVoiceNote.id == KEY).values(**changes))
                await session.commit()
            with pytest.raises(WatchVoiceError, match="receipts_incomplete|publication_conflict"):
                await _replay_adapter(factory, operation, submission)
            note, result = await current(factory)
            assert result.state == phase and result.accepted_at == original_accepted_at
            assert bool(result.accepted_at) == (phase == "accepted")
            assert result.transcript == submission["text"] == note.transcript
            assert world.posts == 1 and world.provider_calls == 0
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["claim", "submit"])
def test_prepared_partial_replay_keeps_multipart_recovery_and_exact_acceptance(tmp_path, operation):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            text = "я" * 12000 + "🙂" * 6001
            submission = await _prepare_adapter_text(factory, text)
            world = WorkWorld()
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row, max_parts=1)
                assert (await current(factory))[0].next_part == 1
                partial = await _replay_adapter(factory, operation, submission)
                assert partial["state"] == "prepared" and world.posts == 1
                for _ in range(2):
                    row, _ = await current(factory)
                    await worker.publish_note(factory, client(http), row, max_parts=1)
            final = await _replay_adapter(factory, operation, submission)
            assert final["state"] == "accepted" and final["accepted_at"]
            again = await _replay_adapter(factory, operation, submission)
            assert again == final
            assert world.posts == 3 and world.provider_calls == 0
            assert (await current(factory))[0].transcript == text
    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_status_reports_same_configured_mcp_capability_as_discovery(tmp_path, enabled):
    from app.infrastructure.watch_voice_status import STATUS_SCHEMA, protected_status

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            settings = configuration(gate_watch_voice_work_mcp_enabled=enabled)
            async with factory() as session:
                status = await protected_status(session, settings, KEY)
            validate(status, STATUS_SCHEMA)
            expected = "protected_owner_mcp_available" if enabled else "internal_only_not_exposed"
            assert status["coordination"]["work_claim_submit"] == expected
            assert bool(adapter.tool_definitions(settings)) == enabled
    asyncio.run(run())


@pytest.mark.parametrize("missing", ["session", "bind"])
def test_missing_database_binding_is_typed_and_redacted(missing):
    async def run():
        session = None if missing == "session" else AsyncSession()
        try:
            with pytest.raises(WatchVoiceError, match="watch_voice_work_database_unavailable"):
                await adapter.call_tool(adapter.TOOL_NAMES[0], arguments(), principal(),
                                        session, configuration())
        finally:
            if session is not None:
                await session.close()
    asyncio.run(run())
