"""Explicit JSON output schemas for the Gate MCP tool contract."""

from __future__ import annotations

from typing import Any

JsonObject = dict[str, Any]


def _object(
    properties: JsonObject,
    *,
    required: list[str] | None = None,
    additional_properties: bool = False,
) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": additional_properties,
    }


def _nullable(value_type: str) -> JsonObject:
    return {"type": [value_type, "null"]}


def _ref(name: str) -> JsonObject:
    return {"$ref": f"#/$defs/{name}"}


def _error_schema() -> JsonObject:
    return _object({"error": {"type": "string", "minLength": 1}})


def _success_or_error(success: JsonObject, definitions: JsonObject) -> JsonObject:
    return {
        "type": "object",
        "$defs": definitions,
        "oneOf": [success, _error_schema()],
    }


ACCOUNT_SCHEMA = _object(
    {
        "id": {"type": "string", "format": "uuid"},
        "alias": {"type": "string", "minLength": 1},
        "label": {"type": "string", "minLength": 1},
    }
)
ACCOUNT_LABEL_SCHEMA = _object(
    {
        "alias": {"type": "string", "minLength": 1},
        "label": {"type": "string", "minLength": 1},
    }
)
COUNTERPARTY_SCHEMA = _object(
    {
        "platform": {"type": "string", "const": "telegram"},
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
COMMON_DEFINITIONS: JsonObject = {
    "account": ACCOUNT_SCHEMA,
    "account_label": ACCOUNT_LABEL_SCHEMA,
    "conversation": CONVERSATION_SCHEMA,
    "counterparty": COUNTERPARTY_SCHEMA,
    "message": MESSAGE_SCHEMA,
    "message_with_counterparty": MESSAGE_WITH_COUNTERPARTY_SCHEMA,
}


GATE_STATUS_OUTPUT_SCHEMA: JsonObject = {
    "type": "object",
    "$defs": {
        "account": ACCOUNT_SCHEMA,
        "connection_health": _object(
            {
                "account": _ref("account"),
                "state": {
                    "type": "string",
                    "enum": ["ready", "degraded", "cannot_reply", "inactive", "missing"],
                },
                "connected": {"type": "boolean"},
                "can_reply": {"type": "boolean"},
                "last_checked_at": _nullable("string"),
                "last_error": _nullable("string"),
            }
        ),
        "contact_directory": _object(
            {
                "account": _ref("account"),
                "imported_contacts": {"type": "integer", "minimum": 0},
                "last_source": _nullable("string"),
                "last_import_at": _nullable("string"),
            }
        ),
    },
    **_object(
        {
            "service": {"type": "string", "const": "gate"},
            "environment": {"type": "string", "minLength": 1},
            "commit": {"type": "string", "minLength": 1},
            "bot_username": {"type": "string", "minLength": 1},
            "default_mode": {"type": "string", "minLength": 1},
            "send_enabled": {"type": "boolean"},
            "access": _object(
                {
                    "subject": {"type": "string", "minLength": 1},
                    "workspace_id": {"type": "string", "minLength": 1},
                    "account_count": {"type": "integer", "minimum": 0},
                    "project_count": {"type": "integer", "minimum": 0},
                }
            ),
            "counts": _object(
                {
                    "accounts": {"type": "integer", "minimum": 0},
                    "business_connections": {"type": "integer", "minimum": 0},
                    "active_business_connections": {"type": "integer", "minimum": 0},
                    "conversations": {"type": "integer", "minimum": 0},
                    "messages": {"type": "integer", "minimum": 0},
                    "reply_tasks": {"type": "integer", "minimum": 0},
                    "unanswered": {"type": "integer", "minimum": 0},
                    "answered_messages": {"type": "integer", "minimum": 0},
                    "drafts": {"type": "integer", "minimum": 0},
                }
            ),
            "connection_health": {
                "type": "array",
                "items": _ref("connection_health"),
            },
            "contact_directory": {
                "type": "array",
                "items": _ref("contact_directory"),
            },
            "alpha_canary": _object(
                {
                    "telegram_worker_enabled": {"type": "boolean"},
                    "connector_enabled": {"type": "boolean"},
                    "shadow_worker_enabled": {"type": "boolean"},
                    "canary_enabled": {"type": "boolean"},
                    "delivery_enabled": {"type": "boolean"},
                    "privacy_mode_confirmed": {"type": "boolean"},
                    "webhook_credentials_configured": {"type": "boolean"},
                    "one_group_discovered": {"type": "boolean"},
                    "one_active_exact_revision": {"type": "boolean"},
                    "audit": _object(
                        {
                            "canonical_messages": {"type": "integer", "minimum": 0},
                            "agent_turns": {"type": "integer", "minimum": 0},
                            "delivery_grants": {"type": "integer", "minimum": 0},
                            "send_attempts": {"type": "integer", "minimum": 0},
                        }
                    ),
                }
            ),
            "mcp": _object(
                {
                    "protocol_version": {"type": "string", "minLength": 1},
                    "tools": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "gate_get_status",
                                "gate_list_unanswered",
                                "gate_get_reply_context",
                                "gate_save_reply_draft",
                                "gate_send_approved_reply",
                            ],
                        },
                        "minItems": 5,
                        "maxItems": 5,
                        "uniqueItems": True,
                    },
                    "draft_writes_enabled": {"type": "boolean"},
                    "send_enabled": {"type": "boolean"},
                    "send_requires_explicit_confirmation": {"type": "boolean"},
                    "auth": {"type": "string", "minLength": 1},
                }
            ),
        }
    ),
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
        "task_id": {"type": "string", "format": "uuid"},
        "task_ref": {"type": "string", "minLength": 1, "maxLength": 8},
        "status": {"type": "string", "minLength": 1},
        "reply_required": {"type": "boolean"},
        "account": _ref("account"),
        "conversation": _ref("conversation"),
        "counterparty": _ref("counterparty"),
        "messages": {
            "type": "array",
            "items": _ref("message_with_counterparty"),
        },
        "count": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
    }
)
GATE_REPLY_CONTEXT_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    REPLY_CONTEXT_SUCCESS_SCHEMA,
    COMMON_DEFINITIONS,
)


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
        "text": {"type": "string", "minLength": 1, "maxLength": 4096},
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
GATE_SEND_OUTPUT_SCHEMA: JsonObject = _success_or_error(
    SEND_SUCCESS_SCHEMA,
    {"account_label": ACCOUNT_LABEL_SCHEMA},
)


MCP_TOOL_OUTPUT_SCHEMAS: dict[str, JsonObject] = {
    "gate_get_status": GATE_STATUS_OUTPUT_SCHEMA,
    "gate_list_unanswered": GATE_UNANSWERED_OUTPUT_SCHEMA,
    "gate_get_reply_context": GATE_REPLY_CONTEXT_OUTPUT_SCHEMA,
    "gate_save_reply_draft": GATE_DRAFT_OUTPUT_SCHEMA,
    "gate_send_approved_reply": GATE_SEND_OUTPUT_SCHEMA,
}
