# mypy: ignore-errors
"""Extend the established Gate MCP surface with private Alfa transcription tools."""

from __future__ import annotations

import hashlib
import mimetypes
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.mcp_routes import (
    _jsonrpc_error,
    _require_mcp_principal,
    _tool_result,
    mcp_endpoint as legacy_mcp_endpoint,
)
from app.api.routes import get_telegram_client
from app.application.alpha_transcription_product import (
    MAX_SOURCE_BYTES,
    AlphaTranscriptionError,
    CreateAlphaTranscriptionCommand,
    safe_title,
    validate_source_filename,
)
from app.core.config import Settings, get_settings
from app.core.mcp_oauth import McpOAuthError
from app.infrastructure.alpha_transcription_jobs import (
    artifact_path,
    create_transcription_job,
    get_transcription_job,
    job_payload,
    list_transcription_jobs,
    retry_failed_transcription_job,
)
from app.infrastructure.dropbox_transcription_storage import DropboxTranscriptionStorage
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)
JsonObject = dict[str, Any]

_CREATE = "gate_alpha_create_transcription"
_GET = "gate_alpha_get_transcription"
_LIST = "gate_alpha_list_transcriptions"
_ARTIFACT = "gate_alpha_get_transcription_artifact"
_RETRY = "gate_alpha_retry_transcription"
_SCOPES = {
    _CREATE: "gate:agents:manage",
    _GET: "gate:status:read",
    _LIST: "gate:status:read",
    _ARTIFACT: "gate:status:read",
    _RETRY: "gate:agents:manage",
}


@router.post("/mcp")
async def mcp_transcription_endpoint(
    payload: JsonObject,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    method = payload.get("method")
    if method == "tools/list":
        response = await legacy_mcp_endpoint(
            payload=payload,
            authorization=authorization,
            settings=settings,
            session=session,
            telegram=telegram,
        )
        tools = response.get("result", {}).get("tools")
        if isinstance(tools, list):
            tools.extend(_tool_definitions())
        return response
    if method != "tools/call":
        return await legacy_mcp_endpoint(
            payload=payload,
            authorization=authorization,
            settings=settings,
            session=session,
            telegram=telegram,
        )
    params = payload.get("params")
    tool_name = params.get("name") if isinstance(params, dict) else None
    if tool_name not in _SCOPES:
        return await legacy_mcp_endpoint(
            payload=payload,
            authorization=authorization,
            settings=settings,
            session=session,
            telegram=telegram,
        )
    request_id = payload.get("id")
    arguments = params.get("arguments") if isinstance(params, dict) else None
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        principal = await _require_mcp_principal(
            authorization=authorization,
            settings=settings,
            required_scope=_SCOPES[tool_name],
            session=session,
        )
        if tool_name == _CREATE:
            return await _create_tool(
                request_id=request_id,
                arguments=arguments,
                workspace_id=principal.access_scope.workspace_id,
                session=session,
                settings=settings,
            )
        if tool_name == _GET:
            return await _get_tool(
                request_id=request_id,
                arguments=arguments,
                workspace_id=principal.access_scope.workspace_id,
                session=session,
            )
        if tool_name == _LIST:
            return await _list_tool(
                request_id=request_id,
                arguments=arguments,
                workspace_id=principal.access_scope.workspace_id,
                session=session,
            )
        if tool_name == _ARTIFACT:
            return await _artifact_tool(
                request_id=request_id,
                arguments=arguments,
                workspace_id=principal.access_scope.workspace_id,
                session=session,
                settings=settings,
            )
        if tool_name == _RETRY:
            return await _retry_tool(
                request_id=request_id,
                arguments=arguments,
                workspace_id=principal.access_scope.workspace_id,
                session=session,
            )
    except (AlphaTranscriptionError, ValueError) as exc:
        code = getattr(exc, "code", str(exc)) or "alpha_transcription_request_invalid"
        return _tool_result({"error": code}, f"Ошибка: {code}", request_id)
    except McpOAuthError as exc:
        return _jsonrpc_error(
            request_id=request_id,
            code=-32001,
            message=f"OAuth required: {exc.error}",
        )
    return _jsonrpc_error(request_id=request_id, code=-32601, message="Unknown tool")


@router.post("/alpha/transcriptions/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_alpha_transcription(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_filename: Annotated[str | None, Header(alias="X-Filename")] = None,
    x_title: Annotated[str | None, Header(alias="X-Title")] = None,
    x_analysis: Annotated[str | None, Header(alias="X-Analysis")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
) -> JsonObject:
    principal = await _require_mcp_principal(
        authorization=authorization,
        settings=settings,
        required_scope="gate:agents:manage",
        session=session,
    )
    filename = validate_source_filename(x_filename or "")
    media_type = request.headers.get(
        "content-type", "application/octet-stream"
    ).split(";", 1)[0]
    analysis_requested = (x_analysis or "").casefold() in {
        "1",
        "true",
        "yes",
        "да",
    }
    with tempfile.TemporaryDirectory(prefix="gate-alpha-upload-") as directory:
        source = Path(directory) / filename
        size, digest = await _stream_request_to_file(request, source)
        inbox_path = f"/Alpha/Transcriptions/Inbox/{uuid.uuid4()}/{filename}"
        async with httpx.AsyncClient() as http:
            storage = DropboxTranscriptionStorage(settings=settings, http=http)
            await storage.upload_file(inbox_path, source)
        command = CreateAlphaTranscriptionCommand(
            workspace_id=principal.access_scope.workspace_id,
            source_path=inbox_path,
            source_filename=filename,
            source_media_type=media_type,
            title=safe_title(x_title or "", fallback=filename),
            analysis_requested=analysis_requested,
        )
        job, duplicate = await create_transcription_job(
            session,
            command,
            source_sha256=digest,
            source_size_bytes=size,
        )
    result = job_payload(job)
    result["duplicate"] = duplicate
    return result


async def _create_tool(
    *,
    request_id: Any,
    arguments: JsonObject,
    workspace_id: str,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    path = arguments.get("dropbox_path")
    if not isinstance(path, str):
        raise AlphaTranscriptionError("alpha_transcription_source_path_invalid")
    async with httpx.AsyncClient() as http:
        entry = await DropboxTranscriptionStorage(
            settings=settings,
            http=http,
        ).stat(path)
    media_type = mimetypes.guess_type(entry.name)[0] or "application/octet-stream"
    command = CreateAlphaTranscriptionCommand(
        workspace_id=workspace_id,
        source_path=entry.path,
        source_filename=validate_source_filename(entry.name),
        source_media_type=media_type,
        title=safe_title(str(arguments.get("title") or ""), fallback=entry.name),
        analysis_requested=bool(arguments.get("analysis", False)),
        language=str(arguments.get("language") or "ru"),
        model=str(arguments.get("model") or "gpt-4o-mini-transcribe"),
    )
    job, duplicate = await create_transcription_job(session, command)
    result = job_payload(job)
    result["duplicate"] = duplicate
    return _tool_result(result, _status_text(result), request_id)


async def _get_tool(
    *,
    request_id: Any,
    arguments: JsonObject,
    workspace_id: str,
    session: AsyncSession,
) -> JsonObject:
    job = await get_transcription_job(
        session,
        workspace_id=workspace_id,
        job_id=str(arguments.get("job_id") or ""),
    )
    if job is None:
        return _tool_result(
            {"error": "alpha_transcription_job_not_found"},
            "Запись не найдена.",
            request_id,
        )
    result = job_payload(job)
    return _tool_result(result, _status_text(result), request_id)


async def _list_tool(
    *,
    request_id: Any,
    arguments: JsonObject,
    workspace_id: str,
    session: AsyncSession,
) -> JsonObject:
    raw_limit = arguments.get("limit", 10)
    limit = raw_limit if type(raw_limit) is int else 10
    jobs = await list_transcription_jobs(
        session,
        workspace_id=workspace_id,
        limit=limit,
    )
    result = {"jobs": [job_payload(job) for job in jobs]}
    text = (
        "\n".join(_status_text(item) for item in result["jobs"])
        or "Расшифровок пока нет."
    )
    return _tool_result(result, text, request_id)


async def _artifact_tool(
    *,
    request_id: Any,
    arguments: JsonObject,
    workspace_id: str,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    job = await get_transcription_job(
        session,
        workspace_id=workspace_id,
        job_id=str(arguments.get("job_id") or ""),
    )
    if job is None:
        return _tool_result(
            {"error": "alpha_transcription_job_not_found"},
            "Запись не найдена.",
            request_id,
        )
    if job.state == "duplicate" and job.canonical_job_id is not None:
        job = await get_transcription_job(
            session,
            workspace_id=workspace_id,
            job_id=str(job.canonical_job_id),
        )
        if job is None:
            return _tool_result(
                {"error": "alpha_transcription_canonical_job_missing"},
                "Каноническая запись не найдена.",
                request_id,
            )
    artifact = str(arguments.get("artifact") or "verbatim.md")
    path = artifact_path(job, artifact)
    if path is None:
        return _tool_result(
            {"error": "alpha_transcription_artifact_unavailable"},
            "Артефакт ещё недоступен.",
            request_id,
        )
    offset = _bounded_int(
        arguments.get("offset"),
        default=0,
        minimum=0,
        maximum=10_000_000,
    )
    limit = _bounded_int(
        arguments.get("limit"),
        default=40_000,
        minimum=1,
        maximum=100_000,
    )
    async with httpx.AsyncClient() as http:
        data = await DropboxTranscriptionStorage(
            settings=settings,
            http=http,
        ).download_bytes(path, maximum=10_000_000)
    text = data.decode("utf-8")
    excerpt = text[offset : offset + limit]
    result = {
        "job_id": str(job.id),
        "artifact": artifact,
        "offset": offset,
        "returned_chars": len(excerpt),
        "total_chars": len(text),
        "has_more": offset + len(excerpt) < len(text),
        "text": excerpt,
    }
    return _tool_result(result, excerpt or "Артефакт пуст.", request_id)


async def _retry_tool(
    *,
    request_id: Any,
    arguments: JsonObject,
    workspace_id: str,
    session: AsyncSession,
) -> JsonObject:
    job = await get_transcription_job(
        session,
        workspace_id=workspace_id,
        job_id=str(arguments.get("job_id") or ""),
    )
    if job is None:
        return _tool_result(
            {"error": "alpha_transcription_job_not_found"},
            "Запись не найдена.",
            request_id,
        )
    job = await retry_failed_transcription_job(session, job=job)
    result = job_payload(job)
    return _tool_result(result, _status_text(result), request_id)


async def _stream_request_to_file(
    request: Request,
    destination: Path,
) -> tuple[int, str]:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid Content-Length",
            ) from exc
        if declared_size <= 0 or declared_size > MAX_SOURCE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Source exceeds the private limit",
            )
    digest = hashlib.sha256()
    total = 0
    with destination.open("wb") as output:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Source exceeds the private limit",
                )
            digest.update(chunk)
            output.write(chunk)
    if total <= 0:
        raise HTTPException(status_code=422, detail="Empty media")
    return total, digest.hexdigest()


def _tool_definitions() -> list[JsonObject]:
    annotations = {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    tools = [
        {
            "name": _CREATE,
            "description": (
                "Queue one private audio or video file from Dropbox for durable Alfa "
                "transcription. Reuses an existing source digest instead of paying twice."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "dropbox_path": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "maxLength": 255},
                    "analysis": {"type": "boolean", "default": False},
                    "language": {
                        "type": "string",
                        "default": "ru",
                        "maxLength": 32,
                    },
                    "model": {
                        "type": "string",
                        "enum": [
                            "gpt-4o-mini-transcribe",
                            "gpt-4o-transcribe",
                        ],
                        "default": "gpt-4o-mini-transcribe",
                    },
                },
                "required": ["dropbox_path"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": _GET,
            "description": (
                "Read durable status, cost, completeness and available artifacts."
            ),
            "inputSchema": _job_id_schema(),
            "annotations": {**annotations, "readOnlyHint": True},
        },
        {
            "name": _LIST,
            "description": "List recent private Alfa transcription jobs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                    }
                },
                "additionalProperties": False,
            },
            "annotations": {**annotations, "readOnlyHint": True},
        },
        {
            "name": _ARTIFACT,
            "description": (
                "Read a paginated private verbatim, clean, analysis or manifest artifact."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "format": "uuid"},
                    "artifact": {
                        "type": "string",
                        "enum": [
                            "verbatim.md",
                            "clean.md",
                            "analysis.md",
                            "manifest.json",
                        ],
                        "default": "verbatim.md",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100000,
                        "default": 40000,
                    },
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            "annotations": {**annotations, "readOnlyHint": True},
        },
        {
            "name": _RETRY,
            "description": (
                "Explicitly retry only a known failed attempt. Unknown outcomes stay blocked."
            ),
            "inputSchema": _job_id_schema(),
            "annotations": annotations,
        },
    ]
    output_schema = {"type": "object", "additionalProperties": True}
    for tool in tools:
        scope = _SCOPES[tool["name"]]
        security = [{"type": "oauth2", "scopes": [scope]}]
        tool["outputSchema"] = output_schema
        tool["securitySchemes"] = security
        tool["_meta"] = {"securitySchemes": security}
    return tools


def _job_id_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {"job_id": {"type": "string", "format": "uuid"}},
        "required": ["job_id"],
        "additionalProperties": False,
    }


def _status_text(value: JsonObject) -> str:
    return (
        f"{value.get('title', 'Запись')} — {value.get('state', 'unknown')}; "
        f"части {value.get('completed_chunk_count', 0)}/"
        f"{value.get('chunk_count', 0)}; job {value.get('job_id', 'unknown')}"
    )


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int:
        return default
    return max(minimum, min(value, maximum))
