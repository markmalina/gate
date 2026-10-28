"""Source-only status, owner isolation and truthful publication evidence."""

import asyncio
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.api import mcp_routes
from app.api.mcp_schemas import GATE_REPLY_CONTEXT_OUTPUT_SCHEMA
from app.core.config import Settings
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.infrastructure.watch_voice_status import STATUS_SCHEMA, protected_status
from app.models.domain import UNCLASSIFIED_PROJECT_ID
from app.models.watch_voice import WatchVoiceNote, WatchVoiceSource
from jsonschema import validate
from sqlalchemy import event
from tests.test_watch_voice import database as watch_database
from tests.test_watch_voice_inventory import note

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
KEY = str(uuid.UUID(int=1))
CFG = Settings(DROPBOX_CALL_ACCOUNT_ID="owner")


def source(**extra):
    values = dict(
        id=KEY, workspace_key=CFG.gate_workspace_id, account_id="owner",
        file_id="id:synthetic1", revision="synthetic-revision", filename="synthetic.m4a",
        source_path="/Alpha/Voice/synthetic.m4a", size_bytes=3,
        detected_at=NOW, dropbox_modified_at=NOW, state="pending", card_state="pending",
    )
    values.update(extra)
    return WatchVoiceSource(**values)


def principal(workspace=None, projects=None):
    return SimpleNamespace(access_scope=SimpleNamespace(
        workspace_id=workspace or CFG.gate_workspace_id,
        project_ids={UNCLASSIFIED_PROJECT_ID} if projects is None else projects,
    ))


@asynccontextmanager
async def database(tmp_path):
    # Existing fixture uses a disposable PostgreSQL schema when DATABASE_URL is
    # supplied by CI, and an isolated SQLite file locally.
    async with watch_database(tmp_path) as factory:
        yield factory, factory.kw["bind"]


async def snapshot(factory):
    async with factory() as session:
        result = await protected_status(session, CFG, KEY)
    validate(result, STATUS_SCHEMA)
    validate(result, GATE_REPLY_CONTEXT_OUTPUT_SCHEMA)
    return result


def test_pending_card_before_archive_is_visible_without_effects_or_payload(tmp_path, monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient.request", AsyncMock(
        side_effect=AssertionError("status must never access an external service")))

    async def run():
        async with database(tmp_path) as (factory, engine):
            async with factory() as session:
                session.add(source(card_state="saved", card_issue_number=101,
                                   card_issue_url="https://github.com/markmalina/mark/issues/101",
                                   card_received_at=NOW))
                await session.commit()
            queries = []
            event.listen(engine.sync_engine, "before_cursor_execute",
                         lambda conn, cursor, statement, params, context, many:
                         queries.append(statement))
            async with factory() as session:
                response = await mcp_routes._get_reply_context_tool_result(
                    request_id=7, arguments={"task_id": "watch-voice-status:" + KEY},
                    principal=principal(), session=session, settings=CFG)
            result = response["result"]["structuredContent"]
            validate(result, GATE_REPLY_CONTEXT_OUTPUT_SCHEMA)
            assert json.loads(response["result"]["content"][0]["text"]) == result
            assert result["card"]["issue_number"] == 101
            assert result["original"]["state"] == "not_archived"
            assert result["original"]["selector"] is None
            assert result["transcription"]["state"] == "not_started"
            assert result["publication"]["journal_complete"] is False
            assert result["times"]["archived_at"] is None
            assert result["coordination"]["accepted_result"] is None
            assert result["coordination"]["retry_authorized"] is False
            assert len(queries) == 1 and queries[0].lstrip().startswith("SELECT")
            assert "watch_voice_notes.data AS" not in queries[0]
            assert "watch_voice_sources.card_body" not in queries[0]
            assert result["provider_calls"] == result["mutations"] == 0
    asyncio.run(run())


def test_unicode_multipart_partial_then_complete_same_issue_after_new_session(tmp_path):
    text = "я" * 12000 + "🙂" * 6001

    async def run():
        async with database(tmp_path) as (factory, _):
            async with factory() as session:
                session.add(source(state="archived", card_state="saved", card_issue_number=101,
                                   card_issue_url="https://github.com/markmalina/mark/issues/101"))
                session.add(WatchVoiceNote(**note(
                    1, transcript=text, transcription_state="transcribed", transcribed_at=NOW,
                    issue_number=101, issue_url="https://github.com/markmalina/mark/issues/101",
                    next_part=1, delivery_state="delivered", delivered_at=NOW,
                )))
                await session.commit()
            partial = await snapshot(factory)
            assert partial["publication"]["expected_parts"] == 3
            assert not partial["publication"]["journal_complete"]
            assert partial["transcription"]["characters"] == len(text)
            assert partial["transcription"]["utf8_bytes"] == len(text.encode())
            assert partial["transcription"]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
            assert text not in json.dumps(partial)
            async with factory() as session:
                row = await session.get(WatchVoiceNote, KEY)
                row.next_part = 3
                await session.commit()
            complete = await snapshot(factory)
            assert complete["publication"]["journal_complete"]
            assert complete["publication"]["issue_number"] == 101
            assert not complete["publication"]["external_content_verified"]
            assert complete["coordination"]["accepted_result"] is None
    asyncio.run(run())


@pytest.mark.parametrize("transcription,delivery,error", [
    ("received", "pending", "watch_voice_transcription_key_missing"),
    ("processing", "pending", None),
    ("unknown", "unknown", "watch_voice_provider_unknown"),
    ("rejected", "blocked", "watch_voice_transcription_rejected"),
])
def test_failed_and_inflight_states_never_authorize_retry(tmp_path, transcription, delivery, error):
    async def run():
        async with database(tmp_path) as (factory, _):
            async with factory() as session:
                session.add(source(state="archived", card_state="unknown",
                                   card_error="watch_voice_card_inflight_unknown"))
                session.add(WatchVoiceNote(**note(
                    1, transcription_state=transcription, delivery_state=delivery,
                    last_error=error, provider_request_count=1, provider_finished_count=0,
                )))
                await session.commit()
            result = await snapshot(factory)
            assert result["transcription"]["state"] == transcription
            assert result["note_last_error"] == error
            assert result["transcription"]["provider_request_count"] == 1
            assert result["card"]["last_error"] == "watch_voice_card_inflight_unknown"
            assert not result["publication"]["journal_complete"]
            assert not result["coordination"]["retry_authorized"]
    asyncio.run(run())


def test_legacy_archive_without_source_retains_exact_selectors_and_unknown_evidence(tmp_path):
    async def run():
        async with database(tmp_path) as (factory, _):
            async with factory() as session:
                session.add(WatchVoiceNote(**note(1)))
                await session.commit()
            result = await snapshot(factory)
            assert result["source"]["state"] == "legacy_archive"
            assert result["card"]["state"] == "legacy"
            assert result["original"]["state"] == "archived_unverified"
            assert result["original"]["selector"] == "watch-voice-file:" + KEY
            assert not result["original"]["integrity_verified"]
            assert result["times"]["detected_at"] is None
            assert result["times"]["recording_started_at"] is None
            assert result["times"]["dropbox_modified_at"] is not None
            assert result["transcription"]["provider_request_count"] is None
    asyncio.run(run())


def test_bad_archive_size_hides_original_selector_without_hydrating_audio(tmp_path):
    async def run():
        async with database(tmp_path) as (factory, _):
            async with factory() as session:
                session.add(WatchVoiceNote(**note(1, data=b"changed-original")))
                await session.commit()
            result = await snapshot(factory)
            assert result["original"]["state"] == "archive_size_conflict"
            assert result["original"]["selector"] is None
    asyncio.run(run())


@pytest.mark.parametrize("scope", [dict(account_id="foreign"), dict(workspace_key="foreign")])
def test_foreign_source_and_note_are_not_found(tmp_path, scope):
    async def run():
        async with database(tmp_path) as (factory, _):
            async with factory() as session:
                session.add(source(**scope))
                session.add(WatchVoiceNote(**note(1, **scope)))
                await session.commit()
            async with factory() as session:
                assert await protected_status(session, CFG, KEY) is None
    asyncio.run(run())


@pytest.mark.parametrize("kwargs", [dict(workspace="foreign"), dict(projects=set())])
def test_route_denies_foreign_scope_before_database_io(kwargs):
    session = AsyncMock()
    result = asyncio.run(mcp_routes._watch_voice_tool_result(
        request_id=1, selector="watch-voice-status:" + KEY, principal=principal(**kwargs),
        session=session, settings=CFG))
    assert result["result"]["isError"]
    assert session.mock_calls == []


@pytest.mark.parametrize("key", ["latest", "list", "", "invalid"])
def test_status_requires_exact_uuid_before_database_io(key):
    session = AsyncMock()
    result = asyncio.run(mcp_routes._watch_voice_tool_result(
        request_id=1, selector="watch-voice-status:" + key, principal=principal(),
        session=session, settings=CFG))
    assert result["result"]["isError"]
    assert session.mock_calls == []


@pytest.mark.parametrize("change,code", [
    (dict(revision="different-revision"), "watch_voice_status_source_conflict"),
    (dict(issue_number=102, issue_url="https://github.com/markmalina/mark/issues/102"),
     "watch_voice_status_receipt_conflict"),
])
def test_conflicting_journals_fail_closed(tmp_path, change, code):
    async def run():
        async with database(tmp_path) as (factory, _):
            async with factory() as session:
                session.add(source(card_state="saved", card_issue_number=101,
                                   card_issue_url="https://github.com/markmalina/mark/issues/101"))
                session.add(WatchVoiceNote(**note(1, **change)))
                await session.commit()
            async with factory() as session:
                with pytest.raises(WatchVoiceError, match=code):
                    await protected_status(session, CFG, KEY)
    asyncio.run(run())


def test_tool_schema_advertises_read_only_status_and_rejects_latest():
    tool = mcp_routes._gate_get_reply_context_tool(CFG)
    validate({"task_id": "watch-voice-status:" + KEY}, tool["inputSchema"])
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        validate({"task_id": "watch-voice-status:latest"}, tool["inputSchema"])
    assert tool["annotations"]["readOnlyHint"]


def test_missing_source_is_explicit_and_does_not_select_latest(tmp_path):
    async def run():
        async with database(tmp_path) as (factory, _):
            async with factory() as session:
                session.add(source(id=str(uuid.UUID(int=2))))
                await session.commit()
            async with factory() as session:
                result = await mcp_routes._get_reply_context_tool_result(
                    request_id=1, arguments={"task_id": "watch-voice-status:" + KEY},
                    principal=principal(), session=session, settings=CFG)
            assert result["result"]["isError"]
            assert "watch_voice_source_not_found" in json.dumps(result)
    asyncio.run(run())
