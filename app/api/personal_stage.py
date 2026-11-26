"""Standalone Work MCP entry point for the isolated RU staging journal."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from sqlalchemy.exc import SQLAlchemyError

from app.api.mcp_results import (
    MCP_PROTOCOL_VERSION,
    jsonrpc_error,
    jsonrpc_result,
    read_mcp_payload,
)
from app.api.personal_command_mcp import (
    PERSONAL_COMMAND_SCOPES,
    _error,
    _result,
    personal_command_call,
    personal_command_tools,
    personal_oauth_error,
)
from app.api.personal_stage_file import (
    FILE_SCOPE,
    FILE_TOOL,
    FIXTURE,
    FIXTURE_PATH,
    check_attachment,
    file_descriptor,
)
from app.core.config import Settings
from app.core.mcp_oauth import McpOAuthError, _decode_access_token, authenticate_mcp_oauth
from app.core.mcp_security import Principal
from app.infrastructure.database import database_runtime_lifespan
from app.personal_telegram.command_scope import CommandRuntimeError
from app.personal_telegram.work_stage import StageJournal, StageSettings

logger = logging.getLogger("uvicorn.error")


async def _record_command_result(
    *, name: str, authorization: str | None, result: dict[str, Any], oauth: Settings
) -> None:
    content = result.get("result", {}).get("structuredContent", {})
    code = content.get("error")
    if code != "invalid_token":
        logger.info("gate696_s1_command name=%s result=%s", name, code or "accepted")
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 24_000:
        logger.info("gate696_s1_oauth name=%s reason=no_bearer", name)
        return
    try:
        claims = await asyncio.to_thread(_decode_access_token, token, oauth)
    except (InvalidTokenError, PyJWKClientError, OSError, ValueError) as error:
        logger.info("gate696_s1_oauth name=%s reason=%s", name, type(error).__name__)
        return
    logger.info(
        "gate696_s1_oauth name=%s reason=invalid_claims workspace=%s accounts=%s client=%s",
        name,
        oauth.mcp_oauth_workspace_claim in claims,
        oauth.mcp_oauth_account_ids_claim in claims,
        bool(claims.get("azp") or claims.get("client_id")),
    )


def create_app(config: StageSettings | None = None) -> FastAPI:
    stage = config or StageSettings.from_environment()
    oauth = stage.oauth_settings()

    def configured_stage_claims(claims: dict[str, Any]) -> dict[str, Any]:
        # The token is signature/audience/issuer-verified before this function runs.
        # This mapping is confined to the one configured synthetic binding.
        client = claims.get("azp", claims.get("client_id"))
        if claims.get("sub") != stage.owner or client != stage.client_id:
            raise McpOAuthError(error="invalid_token", description="The S1 identity is not bound")
        scoped = dict(claims)
        scoped.setdefault(stage.workspace_claim, stage.workspace)
        scoped.setdefault(stage.account_ids_claim, [str(stage.scope().account_id)])
        return scoped

    async def stage_auth(authorization: str | None, required_scope: str) -> Principal:
        return await authenticate_mcp_oauth(
            authorization=authorization,
            settings=oauth,
            required_scope=required_scope,
            claim_defaults=configured_stage_claims,
        )

    catalog = deepcopy(personal_command_tools(oauth))
    ready = next(tool for tool in catalog if tool["name"] == "gate_personal_command_readiness")
    ready_schema = ready["outputSchema"]["oneOf"][0]
    ready_schema["required"].append("journal_ready")
    ready_schema["properties"].update(
        {
            "journal_ready": {"const": True},
            "test_target": {
                "type": "object",
                "properties": {"grant_ref": {"type": "string"}, "peer_ref": {"type": "string"}},
                "required": ["grant_ref", "peer_ref"],
                "additionalProperties": False,
            },
        }
    )
    ready["description"] = (
        "Report journal readiness and issue a short-lived reference to the approved "
        "synthetic target for this client."
    )
    catalog.append(file_descriptor())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with database_runtime_lifespan(stage.database_url.get_secret_value()) as db:
            journal = StageJournal(db.session_factory, stage)
            await journal.check_binding()
            app.state.journal = journal
            app.state.personal_command_service_factory = journal.for_client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        try:
            async with asyncio.timeout(3):
                await app.state.journal.check_binding()
        except (SQLAlchemyError, OSError, TimeoutError, CommandRuntimeError):
            return JSONResponse(
                {"journal_online": False, "runtime_connected": False}, status_code=503
            )
        return JSONResponse({"journal_online": True, "runtime_connected": False})

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def metadata() -> dict[str, Any]:
        return {
            "resource": stage.resource,
            "authorization_servers": [stage.issuer],
            "scopes_supported": sorted(set(PERSONAL_COMMAND_SCOPES.values())),
            "bearer_methods_supported": ["header"],
        }

    @app.get(FIXTURE_PATH)
    async def fixture() -> Response:
        return Response(
            FIXTURE,
            media_type="text/plain",
            headers={"Content-Disposition": 'attachment; filename="gate-s1.txt"'},
        )

    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        payload = await read_mcp_payload(request, limit=64 * 1024)
        request_id, method = payload.get("id"), payload.get("method")
        if method == "notifications/initialized" and "id" not in payload:
            return Response(status_code=202)
        result: dict[str, Any]
        if method == "initialize":
            result = {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": "gate-personal-s1", "version": "1.0"},
                "capabilities": {"tools": {}},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": catalog}
        elif method == "tools/call":
            params = payload.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return JSONResponse(
                    jsonrpc_error(
                        request_id=request_id, code=-32602, message="Invalid tool parameters"
                    )
                )
            name, args, auth = (
                params["name"],
                params.get("arguments", {}),
                request.headers.get("Authorization"),
            )
            if name in PERSONAL_COMMAND_SCOPES:
                outcome = await personal_command_call(
                    request=request,
                    request_id=request_id,
                    tool_name=name,
                    arguments=args,
                    authorization=auth,
                    settings=oauth,
                    stage_auth=stage_auth,
                )
                await _record_command_result(
                    name=name, authorization=auth, result=outcome, oauth=oauth
                )
                return JSONResponse(outcome)
            if name != FILE_TOOL:
                return JSONResponse(
                    jsonrpc_error(request_id=request_id, code=-32602, message="Unknown tool")
                )
            try:
                principal = await stage_auth(auth, FILE_SCOPE)
                journal: StageJournal = request.app.state.personal_command_service_factory(
                    principal
                )
                journal.require_file_access(principal)
                details = await check_attachment(
                    args, host=stage.file_host, resource=stage.resource
                )
            except McpOAuthError as exc:
                return JSONResponse(personal_oauth_error(request_id, exc.error, FILE_SCOPE, oauth))
            except CommandRuntimeError as exc:
                return JSONResponse(_error(request_id, str(exc)))
            reply = _result(request_id, details, is_error=False)
            reply["result"]["content"].append(
                {
                    "type": "resource_link",
                    "uri": details["download_url"],
                    "name": "gate-s1.txt",
                    "mimeType": "text/plain",
                    "size": len(FIXTURE),
                }
            )
            return JSONResponse(reply)
        else:
            return JSONResponse(
                jsonrpc_error(request_id=request_id, code=-32601, message="Method not found")
            )
        return JSONResponse(jsonrpc_result(request_id=request_id, result=result))

    return app
