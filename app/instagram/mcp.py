"""ChatGPT MCP tools for Instagram publishing and read-only analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.instagram.config import InstagramRuntimeConfig
from app.instagram.confirmation import issue_confirmation, verify_confirmation
from app.instagram.contracts import InstagramContractError, ReelDraft
from app.instagram.diagnostics import diagnose_instagram_transport
from app.instagram.meta import MetaInstagramClient, MetaInstagramError

JsonObject = dict[str, Any]

INSTAGRAM_MCP_TOOL_SCOPES = {
    "gate_instagram_diagnose_setup": "gate:status:read",
    "gate_instagram_list_accounts": "gate:status:read",
    "gate_instagram_prepare_reel": "gate:instagram:publish",
    "gate_instagram_publish_reel": "gate:instagram:publish",
    "gate_instagram_get_reel_insights": "gate:instagram:read",
}


def instagram_mcp_tool_names() -> list[str]:
    config = InstagramRuntimeConfig.from_env()
    if config.enabled:
        return list(INSTAGRAM_MCP_TOOL_SCOPES)
    return ["gate_instagram_diagnose_setup"]


def instagram_mcp_tools(
    *, security_scheme: JsonObject | None = None
) -> list[JsonObject]:
    config = InstagramRuntimeConfig.from_env()
    tools = [_diagnose_tool()]
    if config.enabled:
        tools.extend(
            [_list_accounts_tool(), _prepare_tool(), _publish_tool(), _insights_tool()]
        )
    if security_scheme:
        for tool in tools:
            tool["securitySchemes"] = [security_scheme]
            tool.setdefault("_meta", {})["securitySchemes"] = [security_scheme]
    return tools


async def handle_instagram_mcp_tool(
    *, tool_name: str, arguments: JsonObject
) -> JsonObject:
    config = InstagramRuntimeConfig.from_env()
    if tool_name == "gate_instagram_diagnose_setup":
        return diagnose_instagram_transport(config)
    if not config.ready:
        return _error(
            "configuration_required",
            "Instagram is disabled or its account/signing configuration is incomplete",
        )
    if tool_name == "gate_instagram_list_accounts":
        return {
            "accounts": [
                {"alias": item.alias, "instagram_user_id": item.instagram_user_id}
                for item in config.accounts.values()
            ]
        }
    if tool_name == "gate_instagram_prepare_reel":
        try:
            draft = ReelDraft.build(
                account_alias=arguments.get("account_alias"),
                caption=arguments.get("caption", ""),
                video=arguments.get("video"),
                expires_at=datetime.now(UTC) + timedelta(minutes=20),
            )
            if config.account(draft.account_alias) is None:
                return _error(
                    "account_not_found", "Instagram account alias is not configured"
                )
            return {
                "state": "prepared",
                "account_alias": draft.account_alias,
                "caption": draft.caption,
                "video": draft.video.public_summary(),
                "expires_at": draft.expires_at.isoformat(),
                "confirmation_token": issue_confirmation(
                    draft, secret=config.signing_secret
                ),
                "instruction": (
                    "Show the exact account, file and caption to the user. "
                    "Publish only after explicit confirmation."
                ),
            }
        except InstagramContractError as exc:
            return _error("invalid_arguments", str(exc))
    if tool_name == "gate_instagram_publish_reel":
        if arguments.get("confirmed") is not True:
            return _error("confirmation_required", "Explicit confirmation is required")
        if not config.delivery_enabled:
            return _error("delivery_disabled", "Instagram delivery is disabled")
        try:
            draft = verify_confirmation(
                str(arguments.get("confirmation_token", "")),
                secret=config.signing_secret,
            )
            if arguments.get("expected_account_alias") != draft.account_alias:
                return _error(
                    "confirmation_mismatch",
                    "Confirmed account does not match the prepared Reel",
                )
            account = config.account(draft.account_alias)
            if account is None:
                return _error(
                    "account_not_found", "Instagram account alias is not configured"
                )
            async with httpx.AsyncClient() as http:
                published = await MetaInstagramClient(
                    config=config, http=http
                ).publish_reel(
                    account=account,
                    video_url=draft.video.download_url,
                    caption=draft.caption,
                )
            return {
                "state": published.state.value,
                "media_id": published.media_id,
                "permalink": published.permalink,
                "account_alias": draft.account_alias,
            }
        except InstagramContractError as exc:
            return _error("invalid_confirmation", str(exc))
        except MetaInstagramError as exc:
            return _error(exc.code, str(exc))
    if tool_name == "gate_instagram_get_reel_insights":
        account = config.account(str(arguments.get("account_alias", "")))
        media_id = arguments.get("media_id")
        if account is None or not isinstance(media_id, str) or not media_id:
            return _error(
                "invalid_arguments",
                "Configured account_alias and media_id are required",
            )
        try:
            async with httpx.AsyncClient() as http:
                insights = await MetaInstagramClient(config=config, http=http).insights(
                    account=account, media_id=media_id
                )
            return {
                "media_id": insights.media_id,
                "captured_at": insights.captured_at.isoformat(),
                "metrics": insights.metrics,
            }
        except MetaInstagramError as exc:
            return _error(exc.code, str(exc))
    return _error("unknown_tool", "Unknown Instagram tool")


def _list_accounts_tool() -> JsonObject:
    return {
        "name": "gate_instagram_list_accounts",
        "title": "List connected Instagram accounts",
        "description": "List configured Instagram account aliases without exposing access tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"accounts": {"type": "array", "items": {"type": "object"}}},
            "required": ["accounts"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _diagnose_tool() -> JsonObject:
    return {
        "name": "gate_instagram_diagnose_setup",
        "title": "Diagnose Instagram setup",
        "description": (
            "Report missing Instagram configuration and safe activation order "
            "without exposing secret values."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "additionalProperties": True},
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _prepare_tool() -> JsonObject:
    return {
        "name": "gate_instagram_prepare_reel",
        "title": "Prepare an Instagram Reel",
        "description": (
            "Validate one attached finished video, account alias and final caption. "
            "Does not publish."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "video": {"type": "object"},
                "account_alias": {"type": "string", "minLength": 1},
                "caption": {"type": "string", "maxLength": 2200},
            },
            "required": ["video", "account_alias", "caption"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "additionalProperties": True},
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": {"openai/fileParams": ["video"]},
    }


def _publish_tool() -> JsonObject:
    return {
        "name": "gate_instagram_publish_reel",
        "title": "Publish an approved Instagram Reel",
        "description": (
            "Publish exactly the prepared Reel after explicit user confirmation. "
            "Never retry an unknown outcome automatically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirmation_token": {"type": "string", "minLength": 1},
                "confirmed": {"type": "boolean", "const": True},
                "expected_account_alias": {"type": "string", "minLength": 1},
            },
            "required": ["confirmation_token", "confirmed", "expected_account_alias"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "additionalProperties": True},
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    }


def _insights_tool() -> JsonObject:
    return {
        "name": "gate_instagram_get_reel_insights",
        "title": "Get Instagram Reel insights",
        "description": "Read current metrics for one published Reel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_alias": {"type": "string", "minLength": 1},
                "media_id": {"type": "string", "minLength": 1},
            },
            "required": ["account_alias", "media_id"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "additionalProperties": True},
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }


def _error(code: str, message: str) -> JsonObject:
    return {"error": code, "message": message}
