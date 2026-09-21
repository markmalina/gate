"""Read-only, scoped Dropbox intake diagnostics without audio or source identifiers."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

_SAFE_ERRORS = frozenset(
    {
        "dropbox_call_worker_failed",
        "dropbox_call_inbox_path_invalid",
        "dropbox_call_app_key_missing",
        "dropbox_call_app_secret_missing",
        "dropbox_call_refresh_token_missing",
        "dropbox_call_account_id_missing",
        "dropbox_call_openai_key_missing",
        "dropbox_call_oauth_transport_failed",
        "dropbox_call_oauth_rejected",
        "dropbox_call_oauth_invalid",
        "dropbox_call_identity_transport_failed",
        "dropbox_call_identity_rejected",
        "dropbox_call_identity_invalid",
        "dropbox_call_identity_mismatch",
        "dropbox_call_delta_transport_failed",
        "dropbox_call_delta_rejected",
        "dropbox_call_delta_invalid",
        "dropbox_call_download_transport_failed",
        "dropbox_call_download_rejected",
        "dropbox_call_download_invalid",
        "openai_transcription_response_unknown",
        "openai_transcription_transport_unknown",
        "openai_transcription_rejected_http_400",
        "billing_reservation_replayed",
    }
)
_SAFE_STATUSES = frozenset(
    {"received", "disabled", "rejected", "transcribed", "unknown", "processing"}
)


def _safe_error(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and value in _SAFE_ERRORS else "unclassified_error"


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


async def dropbox_call_operational_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    configured_workspace: str,
    account_id: str,
    project_ids: frozenset[str],
    intake_project_allowed: bool,
    enabled: bool,
    configuration: dict[str, bool],
) -> dict[str, Any]:
    """Inspect persisted state only; never poll Dropbox or call a provider.

    The account-global inbox is visible only to the configured owner workspace
    with access to the intake project. Archive rows additionally retain their
    individual project and source-account fences.
    """

    result: dict[str, Any] = {
        "scope_allowed": False,
        "enabled": False,
        "configuration": {},
        "inbox": None,
        "recent_recordings": [],
        "record_limit": 20,
        "private_content_exposed": False,
        "provider_calls": 0,
        "mutation_allowed": False,
    }
    if workspace_key != configured_workspace or not project_ids or not intake_project_allowed:
        return result
    result.update(scope_allowed=True, enabled=enabled, configuration=configuration)
    if not account_id:
        return result

    state_result = await session.execute(
        text(
            "SELECT cursor IS NOT NULL AS cursor_present, backfill_version, pending, "
            "attempt_count, next_attempt_at, last_error_code, webhook_received_at, "
            "updated_at FROM dropbox_call_inbox_states WHERE account_id = :account_id"
        ),
        {"account_id": account_id},
    )
    state = state_result.mappings().one_or_none()
    if state is not None:
        result["inbox"] = {
            "cursor_present": bool(state["cursor_present"]),
            "backfill_version": state["backfill_version"],
            "pending": bool(state["pending"]),
            "attempt_count": state["attempt_count"],
            "next_attempt_at": _timestamp(state["next_attempt_at"]),
            "last_error_code": _safe_error(state["last_error_code"]),
            "webhook_received_at": _timestamp(state["webhook_received_at"]),
            # This is state activity, not necessarily a successful poll/heartbeat.
            "state_updated_at": _timestamp(state["updated_at"]),
        }

    recordings_result = await session.execute(
        text(
            "SELECT id, source_file_id, source_revision, size_bytes, received_at, "
            "created_at, transcription_status, transcription_reason_code, transcribed_at, "
            "transcript_text IS NOT NULL AS transcript_present, summary_status "
            "FROM phone_call_recordings WHERE workspace_key = :workspace_key "
            "AND source_account_id = :account_id "
            "AND CAST(project_id AS VARCHAR) IN :project_ids "
            "ORDER BY received_at DESC, id DESC LIMIT 20"
        ).bindparams(bindparam("project_ids", expanding=True)),
        {
            "workspace_key": workspace_key,
            "account_id": account_id,
            "project_ids": sorted(project_ids),
        },
    )
    for row in recordings_result.mappings():
        source_key = f"{row['source_file_id']}\n{row['source_revision']}".encode()
        result["recent_recordings"].append(
            {
                "recording_id": str(row["id"]),
                "source_revision_sha256": hashlib.sha256(source_key).hexdigest(),
                "size_bytes": row["size_bytes"],
                "received_at": _timestamp(row["received_at"]),
                "created_at": _timestamp(row["created_at"]),
                "transcription_status": (
                    row["transcription_status"]
                    if row["transcription_status"] in _SAFE_STATUSES
                    else "other"
                ),
                "transcription_reason_code": _safe_error(row["transcription_reason_code"]),
                "transcribed_at": _timestamp(row["transcribed_at"]),
                "transcript_present": bool(row["transcript_present"]),
                "summary_status": (
                    row["summary_status"]
                    if row["summary_status"] in {None, "pending", "summarized", "failed"}
                    else "other"
                ),
            }
        )
    return result
