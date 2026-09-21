"""Scope and privacy regression for the production intake diagnostic read path."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.api.mcp_schemas import GATE_STATUS_OUTPUT_SCHEMA
from app.infrastructure.dropbox_call_status import (
    _timestamp,
    dropbox_call_operational_status,
)
from jsonschema import validate
from sqlalchemy import create_engine, text


class ReadSession:
    def __init__(self) -> None:
        self.connection = create_engine("sqlite://").connect()
        self.reads: list[str] = []
        self.connection.execute(text(
            "CREATE TABLE dropbox_call_inbox_states (account_id TEXT, cursor TEXT, "
            "backfill_version INTEGER, pending BOOLEAN, attempt_count INTEGER, "
            "next_attempt_at TEXT, last_error_code TEXT, webhook_received_at TEXT, updated_at TEXT)"
        ))
        self.connection.execute(text(
            "CREATE TABLE phone_call_recordings (id TEXT, workspace_key TEXT, "
            "source_account_id TEXT, project_id TEXT, source_file_id TEXT, source_revision TEXT, "
            "size_bytes INTEGER, received_at TEXT, created_at TEXT, transcription_status TEXT, "
            "transcription_reason_code TEXT, transcribed_at TEXT, transcript_text TEXT, "
            "summary_status TEXT)"
        ))

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> Any:
        self.reads.append(str(statement))
        assert str(statement).startswith("SELECT ")
        return self.connection.execute(statement, parameters)

    def recording(self, record_id: str, **overrides: Any) -> None:
        row = {
            "id": record_id, "workspace_key": "owner", "source_account_id": "configured",
            "project_id": "allowed", "source_file_id": "private-source", "source_revision": "rev",
            "size_bytes": 1200, "received_at": "2026-09-21", "created_at": "2026-09-21",
            "transcription_status": "unknown", "transcription_reason_code": "private-error-body",
            "transcribed_at": None, "transcript_text": "private-transcript", "summary_status": None,
        }
        row.update(overrides)
        self.connection.execute(text(
            f"INSERT INTO phone_call_recordings ({', '.join(row)}) "
            f"VALUES ({', '.join(':' + name for name in row)})"
        ), row)


def inspect(session: ReadSession, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "workspace_key": "owner", "configured_workspace": "owner",
        "account_id": "configured", "project_ids": frozenset({"allowed"}),
        "intake_project_allowed": True, "enabled": True,
        "configuration": {"refresh_token": True},
    }
    arguments.update(overrides)
    return asyncio.run(dropbox_call_operational_status(session, **arguments))  # type: ignore[arg-type]


def test_status_fences_owner_project_and_source_account_without_payload() -> None:
    session = ReadSession()
    session.recording("visible")
    session.recording("foreign-owner", workspace_key="other")
    session.recording("foreign-project", project_id="other")
    session.recording("foreign-account", source_account_id="other")
    session.connection.execute(text(
        "INSERT INTO dropbox_call_inbox_states VALUES "
        "('configured','private-cursor',1,1,7,NULL,'dropbox_call_oauth_rejected',NULL,NULL)"
    ))
    snapshot = inspect(session)
    assert [row["recording_id"] for row in snapshot["recent_recordings"]] == ["visible"]
    assert snapshot["inbox"]["last_error_code"] == "dropbox_call_oauth_rejected"
    assert snapshot["inbox"]["attempt_count"] == 7
    row = snapshot["recent_recordings"][0]
    assert row["source_revision_sha256"] == hashlib.sha256(b"private-source\nrev").hexdigest()
    assert row["transcription_reason_code"] == "unclassified_error"
    assert row["transcription_status"] == "unknown"
    assert row["transcript_present"] is True
    serialized = json.dumps(snapshot)
    for private in ("private-cursor", "private-source", "private-transcript", "private-error-body"):
        assert private not in serialized
    assert "transcript_text IS NOT NULL" in session.reads[1]
    assert len(session.reads) == 2
    validate(snapshot, GATE_STATUS_OUTPUT_SCHEMA["properties"]["dropbox_calls"])
    session.connection.close()


def test_unauthorized_scopes_do_not_even_read_account_global_state() -> None:
    session = ReadSession()
    for override in (
        {"workspace_key": "other"}, {"project_ids": frozenset()},
        {"intake_project_allowed": False},
    ):
        snapshot = inspect(session, **override)
        assert snapshot["scope_allowed"] is False
        assert snapshot["configuration"] == {}
        assert snapshot["inbox"] is None
        assert snapshot["recent_recordings"] == []
        validate(snapshot, GATE_STATUS_OUTPUT_SCHEMA["properties"]["dropbox_calls"])
    assert session.reads == []
    session.connection.close()


def test_status_is_bounded_and_missing_account_never_scans_all_accounts() -> None:
    session = ReadSession()
    assert inspect(session, account_id="")["inbox"] is None
    assert session.reads == []
    for number in range(25):
        session.recording(f"record-{number:02}")
    snapshot = inspect(session)
    assert len(snapshot["recent_recordings"]) == 20
    assert snapshot["recent_recordings"][0]["recording_id"] == "record-24"
    assert snapshot["recent_recordings"][-1]["recording_id"] == "record-05"
    assert snapshot["provider_calls"] == 0
    assert snapshot["mutation_allowed"] is False
    session.connection.close()


def test_timestamp_serialization_preserves_actual_observation() -> None:
    observed = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    assert _timestamp(observed) == "2026-09-21T08:00:00+00:00"
    assert _timestamp(None) is None
