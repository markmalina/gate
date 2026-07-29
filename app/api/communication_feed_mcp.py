# ruff: noqa
"""Owner-scoped unified communication feed projection for Gate MCP."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import mcp_routes
from app.api.deps import get_db_session
from app.api.mcp_schemas import COMMON_DEFINITIONS, REPLY_CONTEXT_SUCCESS_SCHEMA
from app.api.routes import get_telegram_client
from app.core.config import Settings, get_settings
from app.core.mcp_oauth import McpOAuthError
from app.models.domain import (
    Account,
    Message,
    Person,
    PhoneCallRecording,
    TelegramVoiceArchive,
)
from app.services.telegram import TelegramClient

router = APIRouter()
settings_dependency = Depends(get_settings)
db_session_dependency = Depends(get_db_session)
telegram_client_dependency = Depends(get_telegram_client)

JsonObject = dict[str, Any]
OWNER_FEED_TIMEZONE = "Europe/Berlin"
FEED_PREFIXES = ("feed:day:", "lenta:day:")

FEED_ITEM_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "source_ref": {"type": "string", "minLength": 1, "maxLength": 128},
        "occurred_at": {"type": "string", "format": "date-time"},
        "source": {
            "type": "string",
            "enum": ["telegram_ru", "telegram_by", "telegram", "phone_call"],
        },
        "account_alias": {"type": ["string", "null"]},
        "medium": {"type": "string", "enum": ["text", "voice", "call"]},
        "direction": {
            "type": "string",
            "enum": ["inbound", "outbound", "mixed"],
        },
        "actor": {"type": "string", "minLength": 1, "maxLength": 255},
        "counterparty": {"type": "string", "minLength": 1, "maxLength": 255},
        "summary": {"type": ["string", "null"], "maxLength": 12000},
        "summary_status": {"type": ["string", "null"], "maxLength": 32},
        "outcome": {"type": ["string", "null"], "maxLength": 12000},
        "semantic_input_private": {
            "type": ["string", "null"],
            "maxLength": 100000,
        },
        "semantic_status": {
            "type": "string",
            "enum": ["source_ready", "transcript_pending"],
        },
        "original_available": {"type": "boolean"},
        "transcript_status": {"type": ["string", "null"], "maxLength": 32},
        "transcription_reason_code": {
            "type": ["string", "null"],
            "maxLength": 128,
        },
        "summary_reason_code": {
            "type": ["string", "null"],
            "maxLength": 128,
        },
    },
    "required": [
        "source_ref",
        "occurred_at",
        "source",
        "account_alias",
        "medium",
        "direction",
        "actor",
        "counterparty",
        "summary",
        "summary_status",
        "outcome",
        "semantic_input_private",
        "semantic_status",
        "original_available",
        "transcript_status",
        "transcription_reason_code",
        "summary_reason_code",
    ],
    "additionalProperties": False,
}

FEED_SUCCESS_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "feed": {"type": "string", "const": "lenta"},
        "date": {"type": "string", "format": "date"},
        "timezone": {"type": "string", "const": OWNER_FEED_TIMEZONE},
        "items": {
            "type": "array",
            "items": {"$ref": "#/$defs/communication_feed_item"},
            "maxItems": 500,
        },
        "count": {"type": "integer", "minimum": 0, "maximum": 500},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        "truncated": {"type": "boolean"},
        "semantic_processing_required": {"type": "boolean", "const": True},
        "present_source_verbatim_by_default": {"type": "boolean", "const": False},
    },
    "required": [
        "feed",
        "date",
        "timezone",
        "items",
        "count",
        "limit",
        "truncated",
        "semantic_processing_required",
        "present_source_verbatim_by_default",
    ],
    "additionalProperties": False,
}

FEED_REPLY_CONTEXT_OUTPUT_SCHEMA: JsonObject = {
    "type": "object",
    "$defs": {
        **copy.deepcopy(COMMON_DEFINITIONS),
        "communication_feed_item": FEED_ITEM_SCHEMA,
    },
    "oneOf": [
        REPLY_CONTEXT_SUCCESS_SCHEMA,
        FEED_SUCCESS_SCHEMA,
        {
            "type": "object",
            "properties": {"error": {"type": "string", "minLength": 1}},
            "required": ["error"],
            "additionalProperties": False,
        },
    ],
}


@router.post("/mcp")
async def communication_feed_mcp_endpoint(
    payload: JsonObject,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Settings = settings_dependency,
    session: AsyncSession = db_session_dependency,
    telegram: TelegramClient = telegram_client_dependency,
) -> JsonObject:
    """Intercept only the date-bounded feed selector and delegate other MCP calls."""

    if payload.get("method") == "tools/list":
        result = await _delegate(
            payload=payload,
            authorization=authorization,
            settings=settings,
            session=session,
            telegram=telegram,
        )
        return _decorate_tools_list(result)

    if not _is_feed_call(payload):
        return await _delegate(
            payload=payload,
            authorization=authorization,
            settings=settings,
            session=session,
            telegram=telegram,
        )

    mcp_routes._require_mcp_ready(settings)
    request_id = payload.get("id")
    try:
        principal = await mcp_routes._require_mcp_principal(
            authorization=authorization,
            settings=settings,
            required_scope="gate:inbox:read",
            session=session,
        )
    except McpOAuthError as exc:
        return mcp_routes._oauth_required_tool_result(
            request_id=request_id,
            settings=settings,
            scope="gate:inbox:read",
            error=exc.error,
            description=exc.description,
        )

    params = payload.get("params")
    arguments = params.get("arguments") if isinstance(params, dict) else None
    arguments = arguments if isinstance(arguments, dict) else {}
    try:
        feed_day = _parse_feed_day(arguments.get("task_id"))
        limit = _feed_limit(arguments.get("limit", 200))
    except ValueError as exc:
        return mcp_routes._tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=str(exc),
        )

    feed = await _build_feed_payload(
        session=session,
        principal=principal,
        feed_day=feed_day,
        limit=limit,
    )
    return mcp_routes._tool_result(feed, _format_feed_text(feed), request_id)


async def _delegate(
    *,
    payload: JsonObject,
    authorization: str | None,
    settings: Settings,
    session: AsyncSession,
    telegram: TelegramClient,
) -> JsonObject:
    return await mcp_routes.mcp_endpoint(
        payload=payload,
        authorization=authorization,
        settings=settings,
        session=session,
        telegram=telegram,
    )


def _decorate_tools_list(result: JsonObject) -> JsonObject:
    decorated = copy.deepcopy(result)
    tools = decorated.get("result", {}).get("tools")
    if not isinstance(tools, list):
        return decorated
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("name") != "gate_get_reply_context":
            continue
        tool["title"] = "Get Gate context or communication feed"
        tool["description"] = (
            "Return one scoped reply context, or use feed:day:YYYY-MM-DD to read "
            "Mark's RU/BY Telegram text, voice transcripts and phone-call transcripts "
            "as one chronological owner-only source projection. For feed items, derive "
            "concise meaning from semantic_input_private and never quote it by default."
        )
        properties = tool.get("inputSchema", {}).get("properties")
        if isinstance(properties, dict):
            properties["task_id"] = {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": (
                    "Reply-task UUID, existing exact selector, or feed:day:YYYY-MM-DD"
                ),
            }
            properties["limit"] = {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "default": 200,
            }
        tool["outputSchema"] = FEED_REPLY_CONTEXT_OUTPUT_SCHEMA
    return decorated


def _is_feed_call(payload: JsonObject) -> bool:
    if payload.get("method") != "tools/call":
        return False
    params = payload.get("params")
    if not isinstance(params, dict) or params.get("name") != "gate_get_reply_context":
        return False
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return False
    raw_selector = arguments.get("task_id")
    return isinstance(raw_selector, str) and raw_selector.startswith(FEED_PREFIXES)


def _parse_feed_day(raw_selector: object) -> date:
    if not isinstance(raw_selector, str):
        raise ValueError("feed selector is required")
    prefix = next(
        (candidate for candidate in FEED_PREFIXES if raw_selector.startswith(candidate)),
        None,
    )
    if prefix is None:
        raise ValueError("feed selector must be feed:day:YYYY-MM-DD")
    raw_day = raw_selector.removeprefix(prefix)
    try:
        parsed = date.fromisoformat(raw_day)
    except ValueError:
        raise ValueError("feed selector must contain a valid ISO date") from None
    if parsed.isoformat() != raw_day:
        raise ValueError("feed selector must contain a canonical ISO date")
    return parsed


def _feed_limit(raw_limit: object) -> int:
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        raise ValueError("feed limit must be an integer")
    if not 1 <= raw_limit <= 500:
        raise ValueError("feed limit must be between 1 and 500")
    return raw_limit


async def _build_feed_payload(
    *,
    session: AsyncSession,
    principal: Any,
    feed_day: date,
    limit: int,
) -> JsonObject:
    owner_timezone = ZoneInfo(OWNER_FEED_TIMEZONE)
    start_local = datetime.combine(feed_day, time.min, tzinfo=owner_timezone)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)
    query_limit = min(limit * 2, 1000)

    message_time = func.coalesce(
        Message.received_at,
        Message.sent_at,
        Message.created_at,
    )
    message_result = await session.execute(
        select(Message, Account, Person)
        .join(Account, Account.id == Message.account_id)
        .outerjoin(Person, Person.id == Message.person_id)
        .where(
            Message.account_id.in_(principal.access_scope.account_ids),
            Message.project_id.in_(principal.access_scope.project_ids),
            message_time >= start_utc,
            message_time < end_utc,
        )
        .order_by(message_time, Message.id)
        .limit(query_limit)
    )

    voice_message_join = and_(
        Message.account_id == TelegramVoiceArchive.account_id,
        Message.business_connection_id == TelegramVoiceArchive.business_connection_id,
        Message.chat_id == TelegramVoiceArchive.chat_id,
        Message.telegram_message_id == TelegramVoiceArchive.telegram_message_id,
    )
    voice_result = await session.execute(
        select(TelegramVoiceArchive, Account, Message, Person)
        .join(Account, Account.id == TelegramVoiceArchive.account_id)
        .outerjoin(Message, voice_message_join)
        .outerjoin(Person, Person.id == Message.person_id)
        .where(
            TelegramVoiceArchive.account_id.in_(principal.access_scope.account_ids),
            TelegramVoiceArchive.project_id.in_(principal.access_scope.project_ids),
            TelegramVoiceArchive.received_at >= start_utc,
            TelegramVoiceArchive.received_at < end_utc,
        )
        .order_by(TelegramVoiceArchive.received_at, TelegramVoiceArchive.id)
        .limit(query_limit)
    )

    call_result = await session.execute(
        select(PhoneCallRecording)
        .where(
            PhoneCallRecording.workspace_key == principal.access_scope.workspace_id,
            PhoneCallRecording.project_id.in_(principal.access_scope.project_ids),
            PhoneCallRecording.received_at >= start_utc,
            PhoneCallRecording.received_at < end_utc,
        )
        .order_by(PhoneCallRecording.received_at, PhoneCallRecording.id)
        .limit(query_limit)
    )

    voice_rows = list(voice_result.all())
    voice_keys = {
        _telegram_identity(archive)
        for archive, _account, _message, _person in voice_rows
    }
    items: list[JsonObject] = []
    for message, account, person in message_result.all():
        if _telegram_identity(message) in voice_keys:
            continue
        items.append(_message_feed_item(message, account, person, owner_timezone))
    for archive, account, _message, person in voice_rows:
        items.append(_voice_feed_item(archive, account, person, owner_timezone))
    for row in call_result.all():
        items.append(_call_feed_item(row[0], owner_timezone))

    items.sort(key=lambda item: (item["occurred_at"], item["source_ref"]))
    truncated = len(items) > limit
    visible_items = items[:limit]
    return {
        "feed": "lenta",
        "date": feed_day.isoformat(),
        "timezone": OWNER_FEED_TIMEZONE,
        "items": visible_items,
        "count": len(visible_items),
        "limit": limit,
        "truncated": truncated,
        "semantic_processing_required": True,
        "present_source_verbatim_by_default": False,
    }


def _telegram_identity(record: Any) -> tuple[str, str, str, int | None]:
    return (
        str(record.account_id),
        str(record.business_connection_id or ""),
        str(record.chat_id),
        record.telegram_message_id,
    )


def _message_feed_item(
    message: Any,
    account: Any,
    person: Any,
    owner_timezone: ZoneInfo,
) -> JsonObject:
    direction = str(message.direction)
    contact = _contact_label(person)
    occurred_at = (
        message.sent_at
        if direction == "outbound" and message.sent_at is not None
        else message.received_at or message.sent_at or message.created_at
    )
    actor, counterparty = _participants(direction, contact)
    semantic_input = _clean_text(message.text)
    return _feed_item(
        source_ref=f"telegram-message:{message.id}",
        occurred_at=occurred_at,
        source=_telegram_source(account),
        account_alias=account.alias,
        medium="text",
        direction=direction,
        actor=actor,
        counterparty=counterparty,
        semantic_input=semantic_input,
        summary=None,
        summary_status=None,
        semantic_status="source_ready" if semantic_input else "transcript_pending",
        original_available=False,
        transcript_status=None,
        transcription_reason_code=None,
        summary_reason_code=None,
        owner_timezone=owner_timezone,
    )


def _voice_feed_item(
    archive: Any,
    account: Any,
    person: Any,
    owner_timezone: ZoneInfo,
) -> JsonObject:
    direction = str(archive.direction)
    contact = _contact_label(person)
    actor, counterparty = _participants(direction, contact)
    semantic_input = _clean_text(archive.transcript_text)
    return _feed_item(
        source_ref=f"telegram-voice:{archive.id}",
        occurred_at=archive.received_at,
        source=_telegram_source(account),
        account_alias=account.alias,
        medium="voice",
        direction=direction,
        actor=actor,
        counterparty=counterparty,
        semantic_input=semantic_input,
        summary=_clean_text(getattr(archive, "summary_text", None)),
        summary_status=getattr(archive, "summary_status", None),
        semantic_status="source_ready" if semantic_input else "transcript_pending",
        original_available=True,
        transcript_status=archive.transcription_status,
        transcription_reason_code=getattr(
            archive, "transcription_reason_code", None
        ),
        summary_reason_code=getattr(archive, "summary_reason_code", None),
        owner_timezone=owner_timezone,
    )


def _call_feed_item(recording: Any, owner_timezone: ZoneInfo) -> JsonObject:
    semantic_input = _clean_text(recording.transcript_text)
    return _feed_item(
        source_ref=f"phone-call:{recording.id}",
        occurred_at=recording.received_at,
        source="phone_call",
        account_alias="phone_calls",
        medium="call",
        direction="mixed",
        actor="Марк и собеседник",
        counterparty="Собеседник не определён",
        semantic_input=semantic_input,
        summary=_clean_text(getattr(recording, "summary_text", None)),
        summary_status=getattr(recording, "summary_status", None),
        semantic_status="source_ready" if semantic_input else "transcript_pending",
        original_available=True,
        transcript_status=recording.transcription_status,
        transcription_reason_code=getattr(
            recording, "transcription_reason_code", None
        ),
        summary_reason_code=getattr(recording, "summary_reason_code", None),
        owner_timezone=owner_timezone,
    )


def _feed_item(
    *,
    source_ref: str,
    occurred_at: datetime,
    source: str,
    account_alias: str | None,
    medium: str,
    direction: str,
    actor: str,
    counterparty: str,
    semantic_input: str | None,
    summary: str | None,
    summary_status: str | None,
    semantic_status: str,
    original_available: bool,
    transcript_status: str | None,
    transcription_reason_code: str | None,
    summary_reason_code: str | None,
    owner_timezone: ZoneInfo,
) -> JsonObject:
    return {
        "source_ref": source_ref,
        "occurred_at": occurred_at.astimezone(owner_timezone).isoformat(),
        "source": source,
        "account_alias": account_alias,
        "medium": medium,
        "direction": direction,
        "actor": actor,
        "counterparty": counterparty,
        "summary": summary,
        "summary_status": summary_status,
        "outcome": None,
        "semantic_input_private": semantic_input,
        "semantic_status": semantic_status,
        "original_available": original_available,
        "transcript_status": transcript_status,
        "transcription_reason_code": transcription_reason_code,
        "summary_reason_code": summary_reason_code,
    }


def _telegram_source(account: Any) -> str:
    alias = str(getattr(account, "alias", "")).lower()
    country_code = str(getattr(account, "country_code", "")).lower()
    if country_code == "ru" or alias.endswith("_ru") or "telegram_ru" in alias:
        return "telegram_ru"
    if country_code == "by" or alias.endswith("_by") or "telegram_by" in alias:
        return "telegram_by"
    return "telegram"


def _contact_label(person: Any) -> str:
    value = getattr(person, "display_name", None) if person is not None else None
    if isinstance(value, str) and value.strip():
        return value.strip()[:255]
    return "Контакт Telegram"


def _participants(direction: str, contact: str) -> tuple[str, str]:
    if direction == "outbound":
        return "Марк", contact
    return contact, "Марк"


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _format_feed_text(feed: JsonObject) -> str:
    return (
        f"Лента {feed['date']} ({feed['timezone']}): {feed['count']} событий. "
        "Сформулируй смысл каждого semantic_input_private своими словами; "
        "не показывай исходную формулировку по умолчанию. Сохрани время, "
        "источник, носитель, направление и участников."
    )
