"""Explicit JSON output schemas for the Gate MCP tool contract."""

from __future__ import annotations

from app.api.audio_processing_history import AUDIO_PROCESSING_HISTORY_SCHEMA
from app.api.mcp_media import MEDIA_OUTPUT_SCHEMA
from app.api.mcp_schema_common import ACCOUNT_SCHEMA, JsonObject, _nullable, _object, _ref
from app.api.mcp_status_schema import GATE_STATUS_OUTPUT_SCHEMA as GATE_STATUS_OUTPUT_SCHEMA
from app.application.dialog_snapshots import SNAPSHOT_PATTERN
from app.application.document_formats import DOCUMENT_MIME_TYPES
from app.application.owner_reply_readiness import (
    OWNER_REPLY_READINESS_OUTPUT_SCHEMA,
    OWNER_REPLY_READINESS_TOOL_NAME,
)
from app.application.reply_sequences import MAX_SEQUENCE_DRAFTS, MIN_SEQUENCE_DRAFTS


def _error_schema() -> JsonObject:
    return _object({
        "error": {"type": "string", "minLength": 1},
        "sent": {"type": "boolean", "const": False},
        "current_context": {"type": ["object", "null"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 128},
    }, required=["error"])


def _success_or_error(success: JsonObject, definitions: JsonObject) -> JsonObject:
    return {
        "type": "object",
        "$defs": definitions,
        "oneOf": [success, _error_schema()],
    }


ACCOUNT_LABEL_SCHEMA = _object(
    {
        "alias": {"type": "string", "minLength": 1},
        "label": {"type": "string", "minLength": 1},
    }
)
COUNTERPARTY_SCHEMA = _object(
    {
        "platform": {
            "type": "string",
            "enum": ["telegram", "phone_call_dropbox", "watch_voice_dropbox"],
        },
        "platform_user_id": _nullable("string"),
        "display_name": _nullable("string"),
        "username": _nullable("string"),
        "label": _nullable("string"),
    }
)
MESSAGE_SCHEMA = _object(
    {
        "id": {"type": "string", "format": "uuid"},
        "direction": {"type": "string", "enum": ["inbound", "outbound"]},
        "source": {"type": "string", "minLength": 1},
        "chat_id": {"type": "string", "minLength": 1},
        "sender_id": {"type": "string", "minLength": 1},
        "recipient_id": _nullable("string"),
        "provider_message_id": _nullable("string"),
        "text_preview": {"type": "string", "maxLength": 240},
        "created_at": _nullable("string"),
        "received_at": _nullable("string"),
        "sent_at": _nullable("string"),
    }
)
CONVERSATION_SCHEMA = _object(
    {
        "id": {"type": "string", "format": "uuid"},
        "chat_id": {"type": "string", "minLength": 1},
        "business_connection_id": _nullable("string"),
    }
)
MESSAGE_WITH_COUNTERPARTY_SCHEMA = _object(
    {
        **MESSAGE_SCHEMA["properties"],
        "counterparty": _ref("counterparty"),
    }
)
VOICE_DETAILS_SCHEMA = _object(
    {
        "archive_ref": {"type": "string", "minLength": 1, "maxLength": 160},
        "transcription_status": {
            "type": "string",
            "enum": ["pending", "processing", "transcribed", "failed", "unknown"],
        },
        "transcription_reason_code": _nullable("string"),
        "language": _nullable("string"),
        "transcribed_at": _nullable("string"),
        "duration_seconds": {"type": "integer", "minimum": 0},
    }
)
ATTACHMENT_DETAILS_SCHEMA = _object(
    {
        "index": {"type": "integer", "minimum": 0},
        "kind": {
            "type": "string",
            "enum": [
                "document",
                "photo",
                "audio",
                "video",
                "animation",
                "video_note",
            ],
        },
        "filename": {"type": "string", "minLength": 1, "maxLength": 255},
        "mime_type": {"type": "string", "minLength": 1, "maxLength": 128},
        "size_bytes": {
            "type": ["integer", "null"],
            "minimum": 1,
        },
        "download_available": {"type": "boolean"},
        "download_state": {
            "type": "string",
            "enum": [
                "available_on_request",
                "telegram_cloud_limit_exceeded",
            ],
        },
        "selector": {
            "type": "string",
            "pattern": (
                "^[0-9a-fA-F-]{36}:attachment:"
                "[0-9a-fA-F-]{36}:[0-9]+$"
            ),
        },
    }
)
CONTACT_DETAILS_SCHEMA = _object(
    {
        "first_name": {"type": "string", "minLength": 1, "maxLength": 255},
        "last_name": _nullable("string"),
        "phone_number": {"type": "string", "minLength": 1, "maxLength": 64},
        "user_id": _nullable("string"),
        "vcard_present": {"type": "boolean"},
        "source_ref": {"type": "string", "minLength": 1, "maxLength": 128},
        "source_message_id": {"type": "integer", "minimum": 1},
    }
)
REPLY_DETAILS_SCHEMA = _object(
    {
        "status": {"type": "string", "enum": ["snapshot", "unavailable"]},
        "reason_code": _nullable("string"),
        "provider_message_id": _nullable("string"),
        "author": _nullable("string"),
        "sent_at": _nullable("string"),
        "text": {"type": ["string", "null"], "maxLength": 4096},
        "caption": {"type": ["string", "null"], "maxLength": 1024},
        "attachments": {
            "type": "array",
            "maxItems": 1,
            "items": _object({
                "kind": {"type": "string", "enum": [
                    "document", "photo", "voice", "audio", "video", "animation", "video_note",
                ]},
                "filename": {"type": "string", "minLength": 1, "maxLength": 255},
                "mime_type": _nullable("string"),
                "size_bytes": {"type": ["integer", "null"], "minimum": 1},
            }),
        },
        "quote": {"anyOf": [
            _object({
                "text": {"type": "string", "minLength": 1, "maxLength": 4096},
                "position": {"type": "integer", "minimum": 0},
                "is_manual": {"type": "boolean"},
            }),
            {"type": "null"},
        ]},
    }
)
REPLY_CONTEXT_MESSAGE_SCHEMA = _object(
    {
        **MESSAGE_SCHEMA["properties"],
        "message_type": {
            "type": "string",
            "enum": ["text", "voice", "attachment", "contact"],
        },
        "text": _nullable("string"),
        "text_status": {
            "type": "string",
            "enum": ["available", "no_text", "source_unavailable", "processing_failed"],
        },
        "voice": {
            "anyOf": [
                _ref("voice_details"),
                {"type": "null"},
            ]
        },
        "attachments": {
            "type": "array",
            "items": _ref("attachment_details"),
        },
        "contact": {
            "anyOf": [
                _ref("contact_details"),
                {"type": "null"},
            ]
        },
        "reply_to": REPLY_DETAILS_SCHEMA,
        "counterparty": _ref("counterparty"),
    },
    required=[
        *MESSAGE_SCHEMA["properties"],
        "message_type",
        "text",
        "voice",
        "counterparty",
    ],
)
COMMON_DEFINITIONS: JsonObject = {
    "account": ACCOUNT_SCHEMA,
    "account_label": ACCOUNT_LABEL_SCHEMA,
    "conversation": CONVERSATION_SCHEMA,
    "counterparty": COUNTERPARTY_SCHEMA,
    "message": MESSAGE_SCHEMA,
    "message_with_counterparty": MESSAGE_WITH_COUNTERPARTY_SCHEMA,
}


UNANSWERED_ITEM_SCHEMA = _object(
    {
        "task_id": {"type": "string", "format": "uuid"},
        "task_ref": {"type": "string", "minLength": 1, "maxLength": 8},
        "status": {"type": "string", "minLength": 1},
        "priority": {"type": "integer"},
        "reason": _nullable("string"),
        "pending_message_count": {"type": "integer", "minimum": 1},
        "account_id": {"type": "string", "format": "uuid"},
        "project_id": {"type": "string", "format": "uuid"},
        "account": _ref("account"),
        "conversation": _ref("conversation"),
        "counterparty": _ref("counterparty"),
        "message": _ref("message"),
        "created_at": _nullable("string"),
    }
)
ANSWERED_ITEM_SCHEMA = _object(
    {
        "task_id": {
            "anyOf": [
                {"type": "string", "format": "uuid"},
                {"type": "null"},
            ]
        },
        "task_ref": {
            "anyOf": [
                {"type": "string", "minLength": 1, "maxLength": 8},
                {"type": "null"},
            ]
        },
        "follow_up_available": {"type": "boolean"},
        "account_id": {"type": "string", "format": "uuid"},
        "project_id": {"type": "string", "format": "uuid"},
        "account": _ref("account"),
        "counterparty": _ref("counterparty"),
        "message": _ref("message"),
    }
)
GATE_UNANSWERED_OUTPUT_SCHEMA: JsonObject = {
    "type": "object",
    "$defs": {
        **COMMON_DEFINITIONS,
        "unanswered_item": UNANSWERED_ITEM_SCHEMA,
        "answered_item": ANSWERED_ITEM_SCHEMA,
    },
    **_object(
        {
            "items": {"type": "array", "items": _ref("unanswered_item")},
            "count": {"type": "integer", "minimum": 0},
            "answered_items": {"type": "array", "items": _ref("answered_item")},
            "answered_count": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "next_cursor": _nullable("string"),
        }
    ),
}


REPLY_CONTEXT_SUCCESS_SCHEMA = _object(
    {
        "context_snapshot": {
            "type": ["string", "null"],
            "pattern": SNAPSHOT_PATTERN,
            "description": "Last message ID in this dialog, when ready for reply preparation.",
        },
        "context_ready": {"type": "boolean"},
        "context_reason": {"type": ["string", "null"]},
        "dialog_events_next_cursor": {"type": ["string", "null"]},
        "dialog_events": {"type": "array", "items": {"type": "object"}},
        "dialog_events_has_more": {"type": "boolean"},
        "task_id": {"type": "string", "format": "uuid"},
        "task_ref": {"type": "string", "minLength": 1, "maxLength": 8},
        "status": {"type": "string", "minLength": 1},
        "reply_required": {"type": "boolean"},
        "account": _ref("account"),
        "conversation": _ref("conversation"),
        "counterparty": _ref("counterparty"),
        "messages": {
            "type": "array",
            "items": _ref("reply_context_message"),
        },
        "count": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "next_cursor": _nullable("string"),
        "has_more": {"type": "boolean"},
    }
)
REPLY_CONTEXT_SUCCESS_SCHEMA["properties"]["reply_sequences"] = {
    "type": "array", "items": {"type": "object"},
}
REPLY_CONTEXT_SUCCESS_SCHEMA["properties"]["processing_history"] = AUDIO_PROCESSING_HISTORY_SCHEMA
REPLY_CONTEXT_ERROR_SCHEMA = _object(
    {
        "error": {"type": "string", "minLength": 1},
        "processing_history": AUDIO_PROCESSING_HISTORY_SCHEMA,
    },
    required=["error"],
)
GATE_REPLY_CONTEXT_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    REPLY_CONTEXT_SUCCESS_SCHEMA,
    {
        **COMMON_DEFINITIONS,
        "voice_details": VOICE_DETAILS_SCHEMA,
        "attachment_details": ATTACHMENT_DETAILS_SCHEMA,
        "contact_details": CONTACT_DETAILS_SCHEMA,
        "reply_context_message": REPLY_CONTEXT_MESSAGE_SCHEMA,
    },
)
GATE_REPLY_CONTEXT_OUTPUT_SCHEMA["oneOf"][1] = REPLY_CONTEXT_ERROR_SCHEMA


TELEGRAM_ENTITY_SCHEMA = _object(
    {
        "type": {"type": "string", "enum": [
            "bold", "italic", "strikethrough", "underline", "spoiler",
            "text_link", "code", "pre", "blockquote",
        ]},
        "offset": {"type": "integer", "minimum": 0},
        "length": {"type": "integer", "minimum": 1},
        "url": {"type": "string"},
        "language": {"type": "string"},
    }, required=["type", "offset", "length"],
)
FORMATTING_PREVIEW_SCHEMA = _object(
    {
        "source_text": {"type": "string", "maxLength": 65536},
        "text": {"type": "string", "maxLength": 4096},
        "text_format": {"type": "string", "enum": ["plain", "markdown"]},
        "converter_version": {"type": "string", "const": "gate.telegram-markdown.v1"},
        "entities": {"type": "array", "maxItems": 100, "items": TELEGRAM_ENTITY_SCHEMA},
        "payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
)
RICH_PREVIEW_SCHEMA = _object(
    {
        "source_text": {"type": "string", "maxLength": 65536},
        "text": {"type": "string", "maxLength": 65536},
        "text_format": {"const": "rich"},
        "converter_version": {"const": "gate.telegram-rich.v1"},
        "schema_version": {"const": "gate.rich.v1"},
        "entities": {"type": "array", "maxItems": 0},
        "source_content": {"type": "object"},
        "blocks": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "object"}},
        "scope": _object(
            {
                key: {"type": "string", "minLength": 1}
                for key in (
                    "workspace_id",
                    "account_id",
                    "project_id",
                    "recipient_chat_id",
                    "connection_id",
                    "draft_id",
                )
            }
        ),
        "revision": {"const": 1},
        "limits": {"type": "object"},
        "payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
)
NULLABLE_FORMATTING_PREVIEW_SCHEMA = {
    "anyOf": [FORMATTING_PREVIEW_SCHEMA, RICH_PREVIEW_SCHEMA, {"type": "null"}]
}


DRAFT_SUCCESS_SCHEMA = _object(
    {
        "draft_id": {"type": "string", "format": "uuid"},
        "draft_ref": {"type": "string", "minLength": 1, "maxLength": 8},
        "task_id": {"type": "string", "format": "uuid"},
        "account_id": {"type": "string", "format": "uuid"},
        "project_id": {"type": "string", "format": "uuid"},
        "account": _ref("account_label"),
        "recipient_chat_id": {"type": "string", "minLength": 1, "maxLength": 255},
        "status": {"type": "string", "minLength": 1},
        "text": {"type": "string", "minLength": 1, "maxLength": 65536},
        "sent": {"type": "boolean", "const": False},
        "approval_required": {"type": "boolean", "const": True},
        "send_available": {"type": "boolean"},
        "confirmation": _object(
            {
                "expected_account": {"type": "string", "minLength": 1},
                "expected_recipient_chat_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255,
                },
            }
        ),
    }
)
DRAFT_SUCCESS_SCHEMA["properties"]["formatting"] = NULLABLE_FORMATTING_PREVIEW_SCHEMA
DRAFT_SUCCESS_SCHEMA["properties"]["confirmation"]["properties"]["expected_payload_sha256"] = (
    _nullable("string")
)
GATE_DRAFT_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    DRAFT_SUCCESS_SCHEMA,
    {"account_label": ACCOUNT_LABEL_SCHEMA},
)


SEND_SUCCESS_SCHEMA = _object(
    {
        "attempt_id": {"type": "string", "format": "uuid"},
        "attempt_ref": {"type": "string", "minLength": 1, "maxLength": 8},
        "draft_id": {"type": "string", "format": "uuid"},
        "task_id": {"type": "string", "format": "uuid"},
        "account_id": {"type": "string", "format": "uuid"},
        "project_id": {"type": "string", "format": "uuid"},
        "account": _ref("account_label"),
        "recipient_chat_id": {"type": "string", "minLength": 1, "maxLength": 255},
        "status": {"type": "string", "minLength": 1},
        "sent": {"type": "boolean"},
        "idempotent_replay": {"type": "boolean"},
        "provider_message_id": _nullable("string"),
        "error_code": _nullable("string"),
        "error_detail": _nullable("string"),
    }
)
SEND_SUCCESS_SCHEMA["properties"]["payload_sha256"] = _nullable("string")
SEND_SUCCESS_SCHEMA["properties"].update({
    "delivery_mode": {"enum": [
        "rich", "photo", "album", "rich_photo", "rich_slideshow", "rich_collage"
    ]},
    "draft_revision": {"const": 1},
    "runtime_revision": _nullable("string"), "reservation_time": _nullable("string"),
})
SEND_SUCCESS_SCHEMA["properties"]["media"] = {"type": "object"}
GATE_SEND_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    SEND_SUCCESS_SCHEMA,
    {"account_label": ACCOUNT_LABEL_SCHEMA},
)

GATE_DOCUMENT_PREPARE_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "draft_id": {"type": "string", "format": "uuid"},
            "task_id": {"type": "string", "format": "uuid"},
            "account": {"type": "string", "enum": ["RU", "BY"]},
            "recipient_chat_id": {"type": "string", "minLength": 1},
            "filename": {"type": "string", "minLength": 1},
            "media_type": {"type": "string", "enum": DOCUMENT_MIME_TYPES},
            "size_bytes": {"type": "integer", "minimum": 1},
            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "caption": {"type": "string"},
            "sent": {"type": "boolean", "const": False},
            "approval_required": {"type": "boolean", "const": True},
        }
    ),
    {},
)
GATE_DOCUMENT_SEND_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "attempt_id": {"type": "string", "format": "uuid"},
            "draft_id": {"type": "string", "format": "uuid"},
            "task_id": {"type": "string", "format": "uuid"},
            "account": {"type": "string", "enum": ["RU", "BY"]},
            "recipient_chat_id": {"type": "string", "minLength": 1},
            "filename": {"type": "string", "minLength": 1},
            "media_type": {"type": "string", "enum": DOCUMENT_MIME_TYPES},
            "size_bytes": {"type": "integer", "minimum": 1},
            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "status": {"type": "string", "minLength": 1},
            "sent": {"type": "boolean"},
            "idempotent_replay": {"type": "boolean"},
            "provider_message_id": _nullable("string"),
            "error_code": _nullable("string"),
            "error_detail": _nullable("string"),
        }
    ),
    {},
)

# Formatting fields are optional only for legacy compatibility fixtures.
GATE_DOCUMENT_PREPARE_OUTPUT_SCHEMA["oneOf"][0]["properties"]["formatting"] = (
    NULLABLE_FORMATTING_PREVIEW_SCHEMA
)
GATE_DOCUMENT_SEND_OUTPUT_SCHEMA["oneOf"][0]["properties"]["payload_sha256"] = _nullable("string")


SEQUENCE_SUCCESS_SCHEMA = _object(
    {
        "sequence_id": {"type": "string", "format": "uuid"},
        "sequence_ref": {"type": "string", "minLength": 1, "maxLength": 8},
        "task_id": {"type": "string", "format": "uuid"},
        "account_id": {"type": "string", "format": "uuid"},
        "project_id": {"type": "string", "format": "uuid"},
        "account": _ref("account_label"),
        "recipient_chat_id": {"type": "string", "minLength": 1, "maxLength": 255},
        "interval_seconds": {"type": "integer", "const": 60},
        "queued": {"type": "boolean", "const": True},
        "sent": {"type": "boolean", "const": False},
        "items": {
            "type": "array",
            "minItems": MIN_SEQUENCE_DRAFTS,
            "maxItems": MAX_SEQUENCE_DRAFTS,
            "items": _object(
                {
                    "draft_id": {"type": "string", "format": "uuid"},
                    "position": {"type": "integer", "minimum": 0},
                    "scheduled_at": {"type": "string", "format": "date-time"},
                }
            ),
        },
    }
)
SEQUENCE_SUCCESS_SCHEMA["properties"]["items"]["items"]["properties"]["payload_sha256"] = (
    _nullable("string")
)
GATE_SEQUENCE_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    SEQUENCE_SUCCESS_SCHEMA,
    {"account_label": ACCOUNT_LABEL_SCHEMA},
)


ALPHA_INSTALLATION_SCHEMA = _object(
    {
        "installation_ref": {"type": "string", "minLength": 1, "maxLength": 128},
        "group_label": {"type": "string", "minLength": 1, "maxLength": 255},
        "dialog_kind": {"type": "string", "enum": ["group", "supergroup"]},
        "state": {
            "type": "string",
            "enum": [
                "discovered",
                "pending_approval",
                "active",
                "paused",
                "revoked",
            ],
        },
        "connector_ready": {"type": "boolean"},
        "deployment_key": _nullable("string"),
        "revision_key": _nullable("string"),
        "available_actions": {
            "type": "array",
            "items": {"type": "string", "enum": ["approve", "pause", "resume"]},
            "uniqueItems": True,
        },
        "limits": _object(
            {
                "max_turns_per_window": {"type": "integer", "minimum": 1},
                "rate_window_seconds": {"type": "integer", "minimum": 1},
                "max_tokens_per_turn": {"type": "integer", "minimum": 1},
            }
        ),
        "usage": _object(
            {
                "reserved_turns": {"type": "integer", "minimum": 0},
                "recorded_tokens": {"type": "integer", "minimum": 0},
            }
        ),
    }
)
ALPHA_INSTALLATIONS_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "count": {"type": "integer", "minimum": 0},
            "items": {
                "type": "array",
                "items": {"$ref": "#/$defs/alpha_installation"},
            },
            "provider_dialog_ids_exposed": {"type": "boolean", "const": False},
        }
    ),
    {"alpha_installation": ALPHA_INSTALLATION_SCHEMA},
)
ALPHA_WEBHOOK_STATUS_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "installation_ref": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
            "webhook": _object(
                {
                    "api_ok": {"type": "boolean", "const": True},
                    "bot_username": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 64,
                    },
                    "webhook_configured": {"type": "boolean"},
                    "exact_match": {"type": "boolean"},
                    "actual_origin_match": {"type": "boolean"},
                    "expected_webhook_path": {
                        "type": "string",
                        "const": "/telegram/alpha/webhook",
                    },
                    "pending_update_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "allowed_updates": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 64,
                        },
                        "maxItems": 32,
                    },
                    "max_connections": _nullable("integer"),
                    "has_custom_certificate": {"type": "boolean"},
                    "last_error_present": {"type": "boolean"},
                    "provider_dialog_ids_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "telegram_private_data_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                }
            ),
        }
    ),
    {},
)
ALPHA_CONTROL_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "action": {"type": "string", "enum": ["approve", "pause", "resume"]},
            "installation": {"$ref": "#/$defs/alpha_installation"},
        }
    ),
    {"alpha_installation": ALPHA_INSTALLATION_SCHEMA},
)
ALPHA_PREFLIGHT_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "preflight": _object(
                {
                    "preflight_key": {"type": "string", "minLength": 1},
                    "state": {
                        "type": "string",
                        "enum": ["reserved", "succeeded", "failed"],
                    },
                    "replay": {"type": "boolean"},
                    "telegram_delivery_blocked": {
                        "type": "boolean",
                        "const": True,
                    },
                    "provider_attempts": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1,
                    },
                    "reserved_microusd": {"type": "integer", "minimum": 0},
                    "input_tokens": _nullable("integer"),
                    "output_tokens": _nullable("integer"),
                    "result_kind": _nullable("string"),
                    "result_code": _nullable("string"),
                    "candidate_sha256": _nullable("string"),
                    "error_code": _nullable("string"),
                }
            )
        }
    ),
    {},
)


ALPHA_SHADOW_REVIEW_SCHEMA = _object(
    {
        "turn_ref": {"type": "string", "format": "uuid"},
        "installation_ref": {"type": "string", "minLength": 1, "maxLength": 128},
        "group_label": {"type": "string", "minLength": 1, "maxLength": 255},
        "deployment_key": {"type": "string", "minLength": 1, "maxLength": 128},
        "status": {
            "type": "string",
            "enum": ["queued", "running", "proposed", "failed"],
        },
        "outcome": _nullable("string"),
        "result_code": _nullable("string"),
        "trigger_text": {
            "type": ["string", "null"],
            "maxLength": 4000,
        },
        "input_origin": {
            "type": "string",
            "enum": ["telegram_text", "telegram_voice_transcription"],
        },
        "candidate_text": {
            "type": ["string", "null"],
            "maxLength": 12000,
        },
        "candidate_sha256": _nullable("string"),
        "context_message_count": {"type": "integer", "minimum": 0},
        "created_at": {"type": "string", "format": "date-time"},
        "sent_to_telegram": {"type": "boolean", "const": False},
    }
)
ALPHA_SHADOW_REVIEWS_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "count": {"type": "integer", "minimum": 0, "maximum": 50},
            "items": {
                "type": "array",
                "items": {"$ref": "#/$defs/alpha_shadow_review"},
                "maxItems": 50,
            },
            "telegram_delivery_blocked": {"type": "boolean", "const": True},
            "provider_dialog_ids_exposed": {"type": "boolean", "const": False},
        }
    ),
    {"alpha_shadow_review": ALPHA_SHADOW_REVIEW_SCHEMA},
)


BETA_PROVIDER_PROBE_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "probe": _object(
                {
                    "provider": {
                        "type": "string",
                        "const": "telegram_bot_api",
                    },
                    "provider_calls": {
                        "type": "integer",
                        "const": 2,
                    },
                    "call_surface": _object(
                        {
                            "read_methods": {
                                "type": "array",
                                "const": ["getWebhookInfo", "getMe"],
                            },
                            "mutations_allowed": {
                                "type": "boolean",
                                "const": False,
                            },
                        },
                        required=["read_methods", "mutations_allowed"],
                    ),
                    "webhook": _object(
                        {
                            "configured": {"type": "boolean"},
                            "exact_match": {"type": "boolean"},
                            "actual_origin_match": {"type": "boolean"},
                            "url_host": {
                                "type": "string",
                                "maxLength": 253,
                            },
                            "url_path": {
                                "type": "string",
                                "maxLength": 2048,
                            },
                            "expected_path": {
                                "type": "string",
                                "const": "/telegram/beta/webhook",
                            },
                            "allowed_updates": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 64,
                                },
                                "contains": {"const": "callback_query"},
                                "minContains": 1,
                                "maxItems": 32,
                            },
                            "pending_update_count": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "max_connections": _nullable("integer"),
                            "has_custom_certificate": {
                                "type": "boolean",
                            },
                            "last_error": _object(
                                {
                                    "present": {"type": "boolean"},
                                    "date": _nullable("integer"),
                                    "message": {
                                        "type": ["string", "null"],
                                        "enum": [
                                            "provider_error_redacted",
                                            None,
                                        ],
                                    },
                                }
                            ),
                        }
                    ),
                    "identity": _object(
                        {
                            "username_match": {"type": "boolean"},
                            "can_join_groups": {"type": "boolean"},
                            "can_read_all_group_messages": {
                                "type": "boolean",
                            },
                            "privacy_mode_enabled": {
                                "type": "boolean",
                            },
                            "ordinary_group_messages_available": {
                                "type": "boolean",
                            },
                        }
                    ),
                    "provider_identifiers_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "telegram_private_data_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "secrets_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                }
            ),
            "activation": _object(
                {
                    "state": {
                        "type": "string",
                        "const": "binding_creation_disabled",
                    },
                    "candidate_count": {
                        "type": "integer",
                        "const": 0,
                    },
                    "binding_created": {
                        "type": "boolean",
                        "const": False,
                    },
                    "prompt_required": {
                        "type": "boolean",
                        "const": False,
                    },
                    "prompt_delivery": {
                        "type": "string",
                        "const": "not_attempted",
                    },
                    "private_identifiers_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "private_payload_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "consent_digest_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                }
            ),
        }
    ),
    {},
)


BETA_CHECKPOINT_EXPORT_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "installation_ref": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
            "group_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
            "sequence_start": {"type": "integer", "minimum": 1},
            "sequence_end": {"type": "integer", "minimum": 1},
            "event_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
            },
            "container_encoding": {"type": "string", "const": "base64"},
            "container_base64": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4_000_000,
            },
            "manifest": _object(
                {
                    "schema_version": {
                        "type": "string",
                        "const": "beta.portable-checkpoint-manifest@1",
                    },
                    "group_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    },
                    "sequence_start": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "sequence_end": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "event_count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                    },
                    "container": {
                        "type": "string",
                        "const": "beta.x25519-aes256gcm@1",
                    },
                    "ciphertext_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                    "created_at": {
                        "type": "string",
                        "format": "date-time",
                    },
                }
            ),
            "plaintext_context_exposed": {
                "type": "boolean",
                "const": False,
            },
            "telegram_private_data_exposed": {
                "type": "boolean",
                "const": False,
            },
        }
    ),
    {},
)


ALPHA_OWNER_TEST_PRESENCE_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "installation_ref": {"type": "string", "minLength": 1, "maxLength": 128},
            "group_label": {"type": "string", "minLength": 1, "maxLength": 255},
            "member_count": _nullable("integer"),
            "state": {
                "type": "string",
                "enum": ["owner_only", "third_party_present", "unknown"],
            },
            "observed_at": {"type": "string", "format": "date-time"},
            "participant_identities_exposed": {
                "type": "boolean",
                "const": False,
            },
        }
    ),
    {},
)
ALPHA_OWNER_TEST_DELIVERY_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    _object(
        {
            "installation_ref": {"type": "string", "minLength": 1, "maxLength": 128},
            "turn_ref": {"type": "string", "format": "uuid"},
            "state": {
                "type": "string",
                "enum": ["reserved", "sending", "sent", "failed", "unknown", "blocked"],
            },
            "replay": {"type": "boolean"},
            "provider_message_id_exposed": {
                "type": "boolean",
                "const": False,
            },
            "participant_identities_exposed": {
                "type": "boolean",
                "const": False,
            },
        }
    ),
    {},
)


MCP_TOOL_OUTPUT_SCHEMAS: dict[str, JsonObject] = {
    OWNER_REPLY_READINESS_TOOL_NAME: _success_or_error(OWNER_REPLY_READINESS_OUTPUT_SCHEMA, {}),
    "gate_get_status": GATE_STATUS_OUTPUT_SCHEMA,
    "gate_list_unanswered": GATE_UNANSWERED_OUTPUT_SCHEMA,
    "gate_get_reply_context": GATE_REPLY_CONTEXT_OUTPUT_SCHEMA,
    "gate_save_reply_draft": GATE_DRAFT_OUTPUT_SCHEMA,
    "gate_prepare_reply_document": GATE_DOCUMENT_PREPARE_OUTPUT_SCHEMA,
    "gate_prepare_reply_media": MEDIA_OUTPUT_SCHEMA,
    "gate_preview_reply_media": MEDIA_OUTPUT_SCHEMA,
    "gate_send_approved_reply": GATE_SEND_OUTPUT_SCHEMA,
    "gate_send_approved_document": GATE_DOCUMENT_SEND_OUTPUT_SCHEMA,
    "gate_queue_approved_reply_sequence": GATE_SEQUENCE_OUTPUT_SCHEMA,
    "gate_alpha_list_installations": ALPHA_INSTALLATIONS_OUTPUT_SCHEMA,
    "gate_alpha_get_webhook_status": ALPHA_WEBHOOK_STATUS_OUTPUT_SCHEMA,
    "gate_alpha_control_installation": ALPHA_CONTROL_OUTPUT_SCHEMA,
    "gate_alpha_run_model_preflight": ALPHA_PREFLIGHT_OUTPUT_SCHEMA,
    "gate_alpha_list_shadow_reviews": ALPHA_SHADOW_REVIEWS_OUTPUT_SCHEMA,
    "gate_alpha_get_test_group_presence": ALPHA_OWNER_TEST_PRESENCE_OUTPUT_SCHEMA,
    "gate_alpha_send_review": ALPHA_OWNER_TEST_DELIVERY_OUTPUT_SCHEMA,
    "gate_alpha_verify_review_replay": ALPHA_OWNER_TEST_DELIVERY_OUTPUT_SCHEMA,
    "gate_beta_export_space_checkpoint": BETA_CHECKPOINT_EXPORT_OUTPUT_SCHEMA,
    "gate_beta_get_provider_probe": BETA_PROVIDER_PROBE_OUTPUT_SCHEMA,
}
