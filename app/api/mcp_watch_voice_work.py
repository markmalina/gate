"""Owner OAuth adapter for the default-off Watch Work journal; no provider I/O."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.mcp_security import Principal
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.infrastructure.watch_voice_work import claim_work, start_work, submit_work
from app.models.domain import UNCLASSIFIED_PROJECT_ID

SCOPE = "gate:agents:manage"
TOOL_NAMES = (
    "gate_watch_voice_claim",
    "gate_watch_voice_start",
    "gate_watch_voice_submit",
)


class _ClaimIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$",
                       json_schema_extra={"writeOnly": True})


class _SourceIdentity(_ClaimIdentity):
    revision: str = Field(min_length=1, max_length=255, pattern=r"^\S+$")
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _Transcript(_SourceIdentity):
    text: str = Field(min_length=1, max_length=100_000)
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


_MODELS: dict[str, type[_ClaimIdentity]] = dict(
    zip(TOOL_NAMES, (_SourceIdentity, _ClaimIdentity, _Transcript), strict=True)
)

_RECEIPT_PROPERTIES: dict[str, Any] = {
    "schema": {"const": "watch-voice-work-result.v1"},
    "event_id": {"type": "string", "format": "uuid"},
    "claim_id": {"type": "string", "format": "uuid"},
    "epoch": {"type": "integer", "minimum": 1},
    "state": {"enum": ["claimed", "started", "prepared", "saved", "accepted"]},
    "expires_at": {"type": "string", "format": "date-time"},
    "transcript_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
    "characters": {"type": ["integer", "null"], "minimum": 1, "maximum": 100_000},
    "utf8_bytes": {"type": ["integer", "null"], "minimum": 1, "maximum": 400_000},
    **{name: {"type": ["string", "null"], "format": "date-time"}
       for name in ("prepared_at", "saved_at", "accepted_at")},
    "start_recorded_now": {"type": "boolean"}, "provider_authority": {"const": False},
}
OUTPUT_SCHEMA: dict[str, Any] = {"type": "object", "oneOf": [
    {"type": "object", "properties": _RECEIPT_PROPERTIES,
     "required": list(_RECEIPT_PROPERTIES), "additionalProperties": False},
    {"type": "object", "properties": {"error": {"type": "string"}},
     "required": ["error"], "additionalProperties": False},
]}


def advertised(settings: Settings) -> bool:
    """Discovery grants no authority; expose only with the complete owner binding."""
    return bool(
        settings.gate_mcp_enabled and settings.mcp_auth_mode == "oauth"
        and settings.gate_watch_voice_work_mcp_enabled
        and settings.gate_watch_voice_work_reserve_enabled
        and settings.gate_watch_voice_early_inbox_enabled
        and settings.gate_watch_voice_data_gate_approved
        and settings.gate_watch_voice_work_owner_subject.strip()
        and settings.gate_watch_voice_workspace_key == settings.gate_workspace_id
        and settings.gate_workspace_id
        and settings.dropbox_call_account_id.strip()
    )


def tool_definitions(settings: Settings) -> list[dict[str, Any]]:
    if not advertised(settings):
        return []
    descriptions = (
        "Reserve one exact archived owner Watch source before any Gate STT attempt. "
        "Supply a fresh random token (32–128 URL-safe characters); reuse it after a lost reply. "
        "This reserves work only and grants no provider call.",
        "Record processing start for the same claim before separately authorized Work STT. "
        "Replay never grants a second start. An expired started claim becomes unknown. "
        "This tool does not transcribe audio or authorize a provider call.",
        "Submit complete UTF-8 text and its SHA-256 for the same claimed source. "
        "Prepared means durable in Gate, not saved in Mark or accepted. "
        "The existing outbox later publishes and verifies all parts. Reuse the identical "
        "token and text after a lost reply; never replace an immutable candidate.",
    )
    security = [{"type": "oauth2", "scopes": [SCOPE]}]
    return [{
        "name": name, "description": description,
        "inputSchema": _MODELS[name].model_json_schema(),
        "outputSchema": OUTPUT_SCHEMA,
        "annotations": {"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "securitySchemes": security, "_meta": {"securitySchemes": security},
    } for name, description in zip(TOOL_NAMES, descriptions, strict=True)]


async def call_tool(
    name: str, arguments: Any, principal: Principal, session: AsyncSession, settings: Settings,
) -> dict[str, Any]:
    """Authenticate actor server-side and isolate journal commits from the caller session."""
    if not advertised(settings):
        raise WatchVoiceError("watch_voice_work_mcp_disabled")
    if (principal.auth_method != "oauth2" or SCOPE not in principal.scopes
            or principal.access_scope.workspace_id != settings.gate_workspace_id
            or UNCLASSIFIED_PROJECT_ID not in principal.access_scope.project_ids
            or not secrets.compare_digest(principal.subject.encode(),
                                          settings.gate_watch_voice_work_owner_subject.encode())):
        raise WatchVoiceError("watch_voice_work_scope_denied")
    try:
        model = _MODELS[name].model_validate(arguments)
        if str(UUID(model.event_id)) != model.event_id:
            raise ValueError("noncanonical source")
    except (KeyError, ValidationError, ValueError, TypeError):
        # Never include validation details: inputs can contain private transcript/token.
        raise WatchVoiceError("watch_voice_work_arguments_invalid") from None
    if not isinstance(session.bind, AsyncEngine):
        raise WatchVoiceError("watch_voice_work_database_unavailable")
    actor = "watch-work-oauth-v1:" + hashlib.sha256(json.dumps([
        settings.mcp_authorization_server, settings.mcp_public_resource_url,
        principal.auth_method, principal.access_scope.workspace_id, principal.subject,
    ], separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    values = model.model_dump()
    key = values.pop("event_id")
    operations: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = dict(
        zip(TOOL_NAMES, (claim_work, start_work, submit_work), strict=True)
    )
    operation = operations[name]
    return await operation(settings, factory, key, actor=actor, **values)
