"""Synthetic owner isolation, complete-publication and truthful missing-evidence checks."""
import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.core.config import Settings
from app.infrastructure.watch_voice_inventory import card, decode_cursor, protected_inventory
from app.models.billing import BillingBudgetReservationModel
from app.models.canonical import Workspace
from app.models.watch_voice import WatchVoiceNote
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def note(index, **extra):
    values = dict(
        id=str(uuid.UUID(int=index)), workspace_key="mark-personal", account_id="owner",
        filename="synthetic.m4a", source_path="/Alpha/Voice/synthetic.m4a",
        file_id=f"id:synthetic{index}", revision="synthetic-revision", size_bytes=3,
        media_type="audio/mp4", sha256="0" * 64, data=b"abc", duration_seconds=60,
        dropbox_modified_at=NOW, received_at=NOW, transcription_state="received",
        transcript=None, model=None, transcribed_at=None, delivery_state="pending",
        next_part=0, issue_url=None, github_received_at=None, delivered_at=None,
        last_error=None,
    )
    values.update(extra)
    return values


def test_partial_publication_and_unknown_charge_are_never_complete():
    row = SimpleNamespace(**note(1, transcript="я" * 24001, transcription_state="transcribed",
        transcribed_at=NOW, delivery_state="delivered", next_part=1,
        issue_url="https://github.com/markmalina/mark/issues/1", delivered_at=NOW))
    result = card(row, [SimpleNamespace(state="unknown", settled_microusd=None)])
    assert result["publication"]["expected_parts"] == 3
    assert result["publication"]["journal_complete"] is False
    assert result["cost"]["kind"] == "unknown"
    assert result["cost"]["microusd"] is None
    assert result["attempts"]["provider_request_count"] is None
    assert result["times"]["transcription_started_at"] is None


def test_cost_includes_recovery_and_does_not_confuse_reserve_with_charge():
    row = SimpleNamespace(**note(1))
    result = card(row, [SimpleNamespace(state="released", settled_microusd=None),
                        SimpleNamespace(state="settled", settled_microusd=123)])
    assert result["cost"]["microusd"] == 123
    assert result["cost"]["kind"] == "provider_reported"
    assert result["attempts"]["budget_reservation_count"] == 2
    assert card(row, [])["cost"]["kind"] == "unknown"


@pytest.mark.parametrize("cursor", ["", "!", "e30=", "WzEsMSwyXQ==", 1, True])
def test_invalid_cursor_fails_closed(cursor):
    with pytest.raises((ValueError, TypeError)):
        decode_cursor(cursor)


def test_live_keyset_scopes_every_page_and_never_selects_audio(tmp_path):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "inventory.db"))
        queries = []
        event.listen(engine.sync_engine, "before_cursor_execute",
                     lambda conn, cursor, statement, parameters, context, many:
                     queries.append(statement))
        async with engine.begin() as conn:
            for table in (Workspace.__table__, WatchVoiceNote.__table__,
                          BillingBudgetReservationModel.__table__):
                await conn.run_sync(table.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            for index in range(1, 4):
                session.add(WatchVoiceNote(**note(index)))
            session.add(WatchVoiceNote(**note(4, account_id="foreign-account")))
            session.add(WatchVoiceNote(**note(5, workspace_key="foreign-workspace")))
            await session.commit()
        settings = Settings(DROPBOX_CALL_ACCOUNT_ID="owner")
        async with sessions() as session:
            first = await protected_inventory(session, settings, limit=2)
            assert first["has_more"] and first["count"] == 2
            second = await protected_inventory(session, settings, limit=2,
                                               cursor=first["next_cursor"])
            from app.api.mcp_schemas import GATE_REPLY_CONTEXT_OUTPUT_SCHEMA
            from jsonschema import validate
            validate(first, GATE_REPLY_CONTEXT_OUTPUT_SCHEMA)
            assert second["count"] == 1 and not second["has_more"]
            assert [x["event_id"] for x in first["items"] + second["items"]] == [
                str(uuid.UUID(int=i)) for i in (1, 2, 3)]
            for limit in (0, 51, True, "2"):
                with pytest.raises(ValueError):
                    await protected_inventory(session, settings, limit=limit)
        selects = [q for q in queries if q.startswith("SELECT")]
        assert selects and all("watch_voice_notes.data" not in q for q in selects)
        assert "abc" not in json.dumps(first)
        assert "transcript" not in first["items"][0]
        await engine.dispose()
    asyncio.run(scenario())


def test_inventory_route_denies_foreign_workspace_before_database_io():
    from unittest.mock import AsyncMock

    from app.api.mcp_routes import _watch_voice_tool_result
    from app.models.domain import UNCLASSIFIED_PROJECT_ID

    session = AsyncMock()
    result = asyncio.run(_watch_voice_tool_result(
        request_id="synthetic", selector="watch-voice:list", session=session,
        settings=Settings(), principal=SimpleNamespace(access_scope=SimpleNamespace(
            workspace_id="foreign", project_ids={UNCLASSIFIED_PROJECT_ID})),
    ))
    assert result["result"]["isError"] is True
    assert session.mock_calls == []
