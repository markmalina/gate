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
                    "model_shadow": _object(
                        {
                            "state": {
                                "type": "string",
                                "enum": [
                                    "configuration_incomplete",
                                    "deployment_not_exact",
                                    "budget_exhausted",
                                    "safety_invariant_violated",
                                    "provider_preflight_stuck",
                                    "awaiting_provider_preflight",
                                    "provider_preflight_failed",
                                    "provider_preflight_verified",
                                    "shadow_observed",
                                ],
                            },
                            "prerequisites_ready": {"type": "boolean"},
                            "provider_preflight_verified": {"type": "boolean"},
                            "ready_for_provider_preflight": {"type": "boolean"},
                            "ready_for_shadow_observation": {"type": "boolean"},
                            "runtime_enabled": {"type": "boolean"},
                            "data_gate_approved": {"type": "boolean"},
                            "api_key_configured": {"type": "boolean"},
                            "telegram_delivery_blocked": {"type": "boolean"},
                            "fixture_binding_active": {"type": "boolean"},
                            "staged_deployments": {"type": "integer", "minimum": 0},
                            "model_turns": {"type": "integer", "minimum": 0},
                            "queued_turns": {"type": "integer", "minimum": 0},
                            "running_turns": {"type": "integer", "minimum": 0},
                            "proposed_turns": {"type": "integer", "minimum": 0},
                            "failed_turns": {"type": "integer", "minimum": 0},
                            "model_delivery_grants": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "provider_preflight_successes": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "provider_preflight_failures": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "stuck_provider_preflights": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "last_provider_error_category": {
                                "type": "string",
                                "enum": [
                                    "none",
                                    "credentials",
                                    "rate_limit",
                                    "availability",
                                    "request",
                                    "contract",
                                    "unknown",
                                ],
                            },
                            "alerts": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                            },
                            "promotion_dry_run": _object(
                                {
                                    "execution_mode": {
                                        "type": "string",
                                        "const": "dry_run",
                                    },
                                    "mutation_allowed": {
                                        "type": "boolean",
                                        "const": False,
                                    },
                                    "current_stage": {"type": "string"},
                                    "next_stage": {"type": "string"},
                                    "blocked_by": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "shadow_proposals_required": {
                                        "type": "integer",
                                        "minimum": 1,
                                    },
                                    "shadow_proposals_observed": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "limited_canary_review_ready": {
                                        "type": "boolean",
                                    },
                                    "rollback_target": {
                                        "type": "string",
                                        "const": "fixture_canary",
                                    },
                                    "automatic_stop_conditions": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                }
                            ),
                            "budget": _object(
                                {
                                    "monthly_microusd": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "reserved_microusd": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "remaining_microusd": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                }
                            ),
                        }
                    ),
                }
            ),
            "telegram_voice": _object(
                {
                    "state": {
                        "type": "string",
                        "enum": [
                            "disabled",
                            "configuration_incomplete",
                            "budget_exhausted",
                            "settlement_pending",
                            "reconciliation_required",
                            "ready",
                        ],
                    },
                    "enabled": {"type": "boolean"},
                    "api_key_configured": {"type": "boolean"},
                    "model": {"type": "string", "minLength": 1},
                    "entitlement_active": {"type": "boolean"},
                    "period_key": {"type": "string", "minLength": 1},
                    "hard_limit_microusd": {"type": "integer", "minimum": 0},
                    "settled_microusd": {"type": "integer", "minimum": 0},
                    "reserved_microusd": {"type": "integer", "minimum": 0},
                    "remaining_microusd": {"type": "integer", "minimum": 0},
                    "settled_count": {"type": "integer", "minimum": 0},
                    "open_count": {"type": "integer", "minimum": 0},
                    "unknown_count": {"type": "integer", "minimum": 0},
                    "reconciliation_required": {"type": "boolean"},
                    "received_count": {"type": "integer", "minimum": 0},
                    "processing_count": {"type": "integer", "minimum": 0},
                    "delivered_count": {"type": "integer", "minimum": 0},
                    "retry_count": {"type": "integer", "minimum": 0},
                    "dead_letter_count": {"type": "integer", "minimum": 0},
                    "last_error_code": {
                        "anyOf": [
                            {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 128,
                            },
                            {"type": "null"},
                        ],
                    },
                    "telegram_delivery_allowed": {
                        "type": "boolean",
                        "const": False,
                    },
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
                                "gate_queue_approved_reply_sequence",
                                "gate_alpha_list_installations",
                                "gate_alpha_control_installation",
                                "gate_alpha_run_model_preflight",
                                "gate_alpha_list_shadow_reviews",
                            ],
                        },
                        "minItems": 10,
                        "maxItems": 10,
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
            "minItems": 2,
            "maxItems": 20,
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


MCP_TOOL_OUTPUT_SCHEMAS: dict[str, JsonObject] = {
    "gate_get_status": GATE_STATUS_OUTPUT_SCHEMA,
    "gate_list_unanswered": GATE_UNANSWERED_OUTPUT_SCHEMA,
    "gate_get_reply_context": GATE_REPLY_CONTEXT_OUTPUT_SCHEMA,
    "gate_save_reply_draft": GATE_DRAFT_OUTPUT_SCHEMA,
    "gate_send_approved_reply": GATE_SEND_OUTPUT_SCHEMA,
    "gate_queue_approved_reply_sequence": GATE_SEQUENCE_OUTPUT_SCHEMA,
    "gate_alpha_list_installations": ALPHA_INSTALLATIONS_OUTPUT_SCHEMA,
    "gate_alpha_control_installation": ALPHA_CONTROL_OUTPUT_SCHEMA,
    "gate_alpha_run_model_preflight": ALPHA_PREFLIGHT_OUTPUT_SCHEMA,
    "gate_alpha_list_shadow_reviews": ALPHA_SHADOW_REVIEWS_OUTPUT_SCHEMA,
}
