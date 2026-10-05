"""Explicit JSON output schemas for the Gate MCP tool contract."""

from __future__ import annotations

from typing import Any

from app.application.owner_reply_readiness import (
    OWNER_REPLY_READINESS_OUTPUT_SCHEMA,
    OWNER_REPLY_READINESS_TOOL_NAME,
)
from app.application.reply_sequences import MAX_SEQUENCE_DRAFTS, MIN_SEQUENCE_DRAFTS

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
REPLY_CONTEXT_MESSAGE_SCHEMA = _object(
    {
        **MESSAGE_SCHEMA["properties"],
        "message_type": {
            "type": "string",
            "enum": ["text", "voice", "attachment", "contact"],
        },
        "text": _nullable("string"),
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
                    "groups_within_capacity": {"type": "boolean"},
                    "all_active_bindings_exact": {"type": "boolean"},
                    "reviewed_model_release_ready": {"type": "boolean"},
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
            "beta_katya_space": _object(
                {
                    "state": {
                        "type": "string",
                        "enum": [
                            "connector_not_ready",
                            "configuration_incomplete",
                            "awaiting_binding",
                            "awaiting_consent",
                            "reconciliation_required",
                            "active",
                            "revoked",
                        ],
                    },
                    "gates": _object(
                        {
                            "production": {"type": "boolean"},
                            "connector": {"type": "boolean"},
                            "privacy_mode_confirmed": {"type": "boolean"},
                            "space_enabled": {"type": "boolean"},
                            "space_ready": {"type": "boolean"},
                            "delivery_enabled": {"type": "boolean"},
                        }
                    ),
                    "configured": _object(
                        {
                            "chat_binding": {"type": "boolean"},
                            "subject_binding": {"type": "boolean"},
                            "consent_digest": {"type": "boolean"},
                        }
                    ),
                    "binding_state": {
                        "type": "string",
                        "enum": ["absent", "ready", "consented", "revoked"],
                    },
                    "production_ready": {"type": "boolean"},
                    "event_count": {"type": "integer", "minimum": 0},
                    "consent_count": {"type": "integer", "minimum": 0},
                    "fact_count": {"type": "integer", "minimum": 0},
                    "delivery_sent_count": {"type": "integer", "minimum": 0},
                    "delivery_unknown_count": {"type": "integer", "minimum": 0},
                    "private_identifiers_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "private_payload_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                }
            ),
            "beta_wedding_space": _object(
                {
                    "state": {
                        "type": "string",
                        "enum": [
                            "connector_not_ready",
                            "awaiting_binding",
                            "active",
                            "revoked",
                        ],
                    },
                    "gates": _object(
                        {
                            "production": {"type": "boolean"},
                            "connector": {"type": "boolean"},
                            "privacy_mode_confirmed": {"type": "boolean"},
                            "space_enabled": {"type": "boolean"},
                            "space_ready": {"type": "boolean"},
                            "delivery_enabled": {
                                "type": "boolean",
                            },
                        }
                    ),
                    "configured": _object(
                        {
                            "chat_binding": {"type": "boolean"},
                            "subject_binding": {"type": "boolean"},
                            "activation_digest": {"type": "boolean"},
                        }
                    ),
                    "binding_state": {
                        "type": "string",
                        "enum": ["absent", "consented", "revoked"],
                    },
                    "production_ready": {"type": "boolean"},
                    "event_count": {"type": "integer", "minimum": 0},
                    "fact_count": {"type": "integer", "minimum": 0},
                    "reply_sent_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "reply_unknown_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "private_identifiers_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "private_payload_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                }
            ),
            "universal_mayak": _object(
                {
                    "state": {
                        "type": "string",
                        "enum": [
                            "workspace_unavailable",
                            "disabled",
                            "installation_not_ready",
                            "reconciliation_required",
                            "ready",
                            "active_observed",
                        ],
                    },
                    "workspace_ready": {"type": "boolean"},
                    "gates": _object(
                        {
                            "runtime_enabled": {"type": "boolean"},
                            "model_data_gate_approved": {"type": "boolean"},
                            "delivery_enabled": {"type": "boolean"},
                        }
                    ),
                    "configuration_ready": {"type": "boolean"},
                    "installation": _object(
                        {
                            "total": {"type": "integer", "minimum": 0},
                            "discovered": {"type": "integer", "minimum": 0},
                            "pending_approval": {"type": "integer", "minimum": 0},
                            "active": {"type": "integer", "minimum": 0},
                            "paused": {"type": "integer", "minimum": 0},
                            "revoked": {"type": "integer", "minimum": 0},
                            "exact_active": {"type": "integer", "minimum": 0},
                            "ready": {"type": "boolean"},
                        }
                    ),
                    "owner_route_probe": _object(
                        {
                            "task_present": {"type": "boolean"},
                            "account_ready": {"type": "boolean"},
                            "trigger_message_present": {"type": "boolean"},
                            "projects_allowed": {"type": "boolean"},
                            "canonical_ready": {"type": "boolean"},
                            "private_inbound_ready": {"type": "boolean"},
                            "actor_dialog_ready": {"type": "boolean"},
                            "connector_ready": {"type": "boolean"},
                            "business_connection_ready": {"type": "boolean"},
                            "release_ready": {"type": "boolean"},
                            "ready": {"type": "boolean"},
                        }
                    ),
                    "sessions": _object(
                        {
                            "total": {"type": "integer", "minimum": 0},
                            "active": {"type": "integer", "minimum": 0},
                            "paused": {"type": "integer", "minimum": 0},
                            "completed": {"type": "integer", "minimum": 0},
                            "handoff_required": {"type": "integer", "minimum": 0},
                        }
                    ),
                    "turns": _object(
                        {
                            "total": {"type": "integer", "minimum": 0},
                            "queued": {"type": "integer", "minimum": 0},
                            "running": {"type": "integer", "minimum": 0},
                            "proposed": {"type": "integer", "minimum": 0},
                            "failed": {"type": "integer", "minimum": 0},
                        }
                    ),
                    "delivery_grants": _object(
                        {
                            "total": {"type": "integer", "minimum": 0},
                            "open": {"type": "integer", "minimum": 0},
                            "used": {"type": "integer", "minimum": 0},
                            "revoked": {"type": "integer", "minimum": 0},
                        }
                    ),
                    "send_attempts": _object(
                        {
                            "total": {"type": "integer", "minimum": 0},
                            "reserved": {"type": "integer", "minimum": 0},
                            "sending": {"type": "integer", "minimum": 0},
                            "sent": {"type": "integer", "minimum": 0},
                            "failed": {"type": "integer", "minimum": 0},
                            "unknown": {"type": "integer", "minimum": 0},
                            "blocked": {"type": "integer", "minimum": 0},
                        }
                    ),
                    "last_result_code": _nullable("string"),
                    "last_attempt_state": _nullable("string"),
                    "last_attempt_error_code": _nullable("string"),
                    "reconciliation_required": {"type": "boolean"},
                    "provider_free_replay_verifier": _object(
                        {
                            "available": {"type": "boolean"},
                            "input_digest_bound": {"type": "boolean"},
                            "terminal_state": {"type": "boolean"},
                            "verified": {"type": "boolean"},
                            "provider_call_required": {
                                "type": "boolean",
                                "const": False,
                            },
                        }
                    ),
                    "private_content_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "provider_identifiers_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "telegram_identifiers_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                }
            ),
            "become_controlled_intake": _object(
                {
                    "schema": {
                        "type": "string",
                        "const": "gate.become-controlled-intake@1",
                    },
                    "receipt_count": {"type": "integer", "minimum": 0},
                    "registration_confirmed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "owner_only_count": {"type": "integer", "minimum": 0},
                    "registration_ready": {"type": "boolean"},
                    "consent_accepted_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "consent_ready": {"type": "boolean"},
                    "relations_handoff_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "relations_handoff_ready": {"type": "boolean"},
                    "relations_delivery_enabled": {"type": "boolean"},
                    "relations_delivered_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "relations_delivery_ready": {"type": "boolean"},
                    "relations_delivery_attempts": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "relations_consent_executed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "relations_consent_executed_ready": {
                        "type": "boolean",
                    },
                    "media_prompt_gate_ready": {"type": "boolean"},
                    "media_prompt_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "media_prompt_sent_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "media_prompt_unknown_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "media_prompt_failed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "media_prompt_ready": {"type": "boolean"},
                    "media_prompt_reply_required": {"type": "boolean"},
                    "media_prompt_reconciliation_required": {
                        "type": "boolean",
                    },
                    "media_intake_gate_ready": {"type": "boolean"},
                    "media_intake_ready": {"type": "boolean"},
                    "media_intake_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "media_pending_download_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_intake_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "video_intake_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_correlation_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_correlation_ready": {"type": "boolean"},
                    "chain_slot_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "chain_slots_initialized_ready": {"type": "boolean"},
                    "chain_execution_enabled": {
                        "type": "boolean",
                    },
                    "chain_ready_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "chain_requested_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "chain_unknown_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "chain_failed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "chain_last_failure_code": _nullable("string"),
                    "chain_recovery_receipt_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "chain_recovery_prepared_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "chain_recovery_prepared_receipt_enabled": {
                        "type": "boolean",
                        "const": False,
                    },
                    "chain_recovery_enabled": {"type": "boolean"},
                    "chain_recovery_activation_max_posts": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                    },
                    "chain_recovery_attempt_version": _nullable("integer"),
                    "chain_recovery_target_source_sha": _nullable("string"),
                    "chain_recovery_source_failure_preserved": {
                        "type": "boolean",
                    },
                    "chain_recovery_attempt_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "chain_recovery_activation_receipt_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "chain_recovery_attempt_digest": _nullable("string"),
                    "chain_recovery_prepared_digest": _nullable("string"),
                    "chain_recovery_attempt_state": {
                        "type": "string",
                        "enum": [
                            "prepared",
                            "in_progress",
                            "completed",
                            "failed",
                            "unknown",
                        ],
                    },
                    "chain_recovery_post_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                    },
                    "chain_recovery_reconciliation_required": {
                        "type": "boolean",
                    },
                    "chain_recovery_mayaki_source_sha": {
                        "type": "string",
                        "const": (
                            "2078d230852af1c7e3a309f00d716fa2fc664f68"
                        ),
                    },
                    "chain_recovery_oko_source_sha": {
                        "type": "string",
                        "const": (
                            "2c304529d04be43726e677d2b864c4b5f8283be8"
                        ),
                    },
                    "chain_recovery_take_source_sha": {
                        "type": "string",
                        "const": (
                            "4a81bf8e221929be8b8898ddd52a0cbdbbe2e9dc"
                        ),
                    },
                    "chain_recovery_mayaki_state": {
                        "type": "string",
                        "enum": [
                            "not_started",
                            "reserved",
                            "completed",
                            "failed",
                            "unknown",
                        ],
                    },
                    "chain_recovery_mayaki_post_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "chain_recovery_mayaki_request_digest": _nullable("string"),
                    "chain_recovery_mayaki_receipt_digest": _nullable("string"),
                    "chain_recovery_oko_state": {
                        "type": "string",
                        "enum": [
                            "not_started",
                            "reserved",
                            "completed",
                            "failed",
                            "unknown",
                        ],
                    },
                    "chain_recovery_oko_post_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "chain_recovery_oko_request_digest": _nullable("string"),
                    "chain_recovery_oko_receipt_digest": _nullable("string"),
                    "chain_recovery_take_state": {
                        "type": "string",
                        "enum": [
                            "not_started",
                            "reserved",
                            "completed",
                            "failed",
                            "unknown",
                        ],
                    },
                    "chain_recovery_take_post_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "chain_recovery_take_request_digest": _nullable("string"),
                    "chain_recovery_take_receipt_digest": _nullable("string"),
                    "mayaki_connectivity_proven": {"type": "boolean"},
                    "mayaki_connectivity_error_code": _nullable("string"),
                    "mayaki_service_name": {
                        "type": "string",
                        "const": "become-mayaki-controlled",
                    },
                    "mayaki_private_hostname": {
                        "type": "string",
                        "const": (
                            "become-mayaki-controlled.railway.internal"
                        ),
                    },
                    "mayaki_private_port": {
                        "type": "integer",
                        "const": 3000,
                    },
                    "mayaki_source_sha": {
                        "type": "string",
                        "const": (
                            "2078d230852af1c7e3a309f00d716fa2fc664f68"
                        ),
                    },
                    "mayaki_health_external_effects": {
                        "type": "integer",
                        "const": 0,
                    },
                    "oko_connectivity_proven": {"type": "boolean"},
                    "oko_connectivity_error_code": _nullable("string"),
                    "oko_service_name": {
                        "type": "string",
                        "const": "become-oko-controlled",
                    },
                    "oko_private_hostname": {
                        "type": "string",
                        "const": "become-oko-controlled.railway.internal",
                    },
                    "oko_private_port": {
                        "type": "integer",
                        "const": 3000,
                    },
                    "oko_source_sha": {
                        "type": "string",
                        "const": (
                            "2c304529d04be43726e677d2b864c4b5f8283be8"
                        ),
                    },
                    "oko_health_external_effects": {
                        "type": "integer",
                        "const": 0,
                    },
                    "take_connectivity_proven": {"type": "boolean"},
                    "take_connectivity_error_code": _nullable("string"),
                    "take_service_name": {
                        "type": "string",
                        "const": "become-take-controlled",
                    },
                    "take_private_hostname": {
                        "type": "string",
                        "const": "become-take-controlled.railway.internal",
                    },
                    "take_private_port": {
                        "type": "integer",
                        "const": 8000,
                    },
                    "take_source_sha": {
                        "type": "string",
                        "const": (
                            "4a81bf8e221929be8b8898ddd52a0cbdbbe2e9dc"
                        ),
                    },
                    "take_health_external_effects": {
                        "type": "integer",
                        "const": 0,
                    },
                    "chain_reconciliation_required": {"type": "boolean"},
                    "relations_chain_completed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "mayaki_chain_completed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "oko_chain_completed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "take_chain_completed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "quest_projection_enabled": {
                        "type": "boolean",
                        "const": False,
                    },
                    "quest_activation_max_operations": {
                        "type": "integer",
                        "const": 0,
                    },
                    "quest_role_gate_enabled": {
                        "type": "boolean",
                        "const": False,
                    },
                    "quest_approval_gate_enabled": {
                        "type": "boolean",
                        "const": False,
                    },
                    "quest_count": {"type": "integer", "minimum": 0},
                    "quest_participant_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "quest_approval_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "quest_enrollment_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "quest_attempt_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "quest_reconciliation_required": {
                        "type": "boolean",
                    },
                    "quest_external_effects": {
                        "type": "integer",
                        "const": 0,
                    },
                    "free_result_delivery_enabled": {
                        "type": "boolean",
                        "const": False,
                    },
                    "controlled_test_payment_enabled": {
                        "type": "boolean",
                        "const": False,
                    },
                    "raw_media_retention_days": {
                        "type": "integer",
                        "const": 7,
                    },
                    "raw_media_retained_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "raw_media_purged_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "raw_media_retained_bytes": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "confirmed_transcript_retention_days": {
                        "type": "integer",
                        "const": 30,
                    },
                    "confirmed_transcript_retained_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "confirmed_transcript_purged_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_enabled": {
                        "type": "boolean",
                    },
                    "voice_transcription_data_gate_approved": {
                        "type": "boolean",
                    },
                    "voice_transcription_max_calls": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "voice_transcription_eligible_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_prerequisites_ready": {
                        "type": "boolean",
                    },
                    "voice_transcription_gate_ready": {
                        "type": "boolean",
                    },
                    "voice_transcription_attempt_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_call_boundary_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_confirmed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_rejected_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_unknown_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_blocked_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_settlement_pending_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "voice_transcription_ready": {
                        "type": "boolean",
                    },
                    "voice_transcription_reconciliation_required": {
                        "type": "boolean",
                    },
                    "external_effects": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "provider_calls": {"type": "integer", "minimum": 0},
                    "messages_or_publications": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "media_bytes": {"type": "integer", "minimum": 0},
                    "real_payments": {"type": "integer", "const": 0},
                    "private_content_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "provider_identifiers_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
                    "telegram_identifiers_exposed": {
                        "type": "boolean",
                        "const": False,
                    },
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
                    "pending_count": {"type": "integer", "minimum": 0},
                    "active_processing_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "stale_processing_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "nonterminal_archive_missing_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "nonterminal_archive_pending_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "nonterminal_archive_transcribed_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "nonterminal_archive_other_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "nonterminal_transcript_complete_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "nonterminal_summary_complete_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "transport_unknown_archive_missing_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "transport_unknown_archive_present_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "transport_unknown_transcript_complete_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "transport_unknown_summary_complete_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
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
            "telegram_business_text_parity": _object(
                {
                    "state": {
                        "type": "string",
                        "enum": [
                            "no_evidence",
                            "collecting",
                            "blocked",
                            "ready",
                        ],
                    },
                    "total_count": {"type": "integer", "minimum": 0},
                    "matched_count": {"type": "integer", "minimum": 0},
                    "pending_count": {"type": "integer", "minimum": 0},
                    "mismatch_count": {"type": "integer", "minimum": 0},
                    "required_account_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "covered_account_count": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "mismatch_codes": {
                        "type": "array",
                        "maxItems": 7,
                        "items": _object(
                            {
                                "code": {
                                    "type": "string",
                                    "enum": [
                                        "telegram_business_text_parity_account_mismatch",
                                        "telegram_business_text_parity_direction_mismatch",
                                        "telegram_business_text_parity_dialog_mismatch",
                                        "telegram_business_text_parity_text_mismatch",
                                        "telegram_business_text_parity_reply_task_mismatch",
                                        "telegram_business_text_parity_legacy_cardinality",
                                        "telegram_business_text_parity_legacy_invalid",
                                    ],
                                },
                                "count": {"type": "integer", "minimum": 1},
                            }
                        ),
                    },
                    "cutover_ready": {"type": "boolean"},
                    "private_content_exposed": {
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
                                 "gate_prepare_reply_document",
                                 "gate_send_approved_reply",
                                 "gate_send_approved_document",
                                "gate_queue_approved_reply_sequence",
                                "gate_alpha_list_installations",
                                "gate_alpha_get_webhook_status",
                                "gate_alpha_control_installation",
                                "gate_alpha_run_model_preflight",
                                "gate_alpha_list_shadow_reviews",
                                "gate_alpha_get_test_group_presence",
                                "gate_alpha_send_review",
                                "gate_alpha_verify_review_replay",
                                "gate_beta_export_space_checkpoint",
                                "gate_beta_get_provider_probe",
                            ],
                        },
                        "minItems": 18,
                        "maxItems": 18,
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



HISTORICAL_VOICE_DEBT_RECORD_SCHEMA = _object(
    {
        "receipt_id": {"type": "string", "format": "uuid"},
        "event_id": {"type": "string", "format": "uuid"},
        "archive_id": {"type": "string", "format": "uuid"},
        "record_ref": {"type": "string", "minLength": 16, "maxLength": 16},
        "reason_code": {
            "type": "string",
            "enum": [
                "billing_reservation_replayed",
                "openai_transcription_transport_unknown",
            ],
        },
        "received_at": {"type": "string", "format": "date-time"},
        "last_error_at": _nullable("string"),
        "workspace_id": {"type": "string", "format": "uuid"},
        "account_id": {"type": "string", "format": "uuid"},
        "project_id": {"type": "string", "format": "uuid"},
        "country_code": _nullable("string"),
        "chat_scope_matched": {"type": "boolean"},
        "business_connection_scope_matched": {"type": "boolean"},
        "verified_identity_count": {"type": "integer", "minimum": 0},
        "telegram_identity_confirmed": {"type": "boolean"},
        "logical_operation_sha256": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
        },
        "reservation_count": {"type": "integer", "minimum": 0},
        "reservation_states": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["reserved", "settled", "released", "unknown"],
            },
        },
        "original_reservation_state": {
            "anyOf": [
                {
                    "type": "string",
                    "enum": ["reserved", "settled", "released", "unknown"],
                },
                {"type": "null"},
            ]
        },
        "settlement_count": {"type": "integer", "minimum": 0},
        "release_count": {"type": "integer", "minimum": 0},
        "unknown_count": {"type": "integer", "minimum": 0},
        "usage_event_count": {"type": "integer", "minimum": 0},
        "ledger_entry_count": {"type": "integer", "minimum": 0},
        "provider_attempt_count": {"type": "integer", "minimum": 0},
        "archive_status": _nullable("string"),
        "transcript_complete": {"type": "boolean"},
        "summary_complete": {"type": "boolean"},
        "provider_result_saved": {"type": "boolean"},
        "canonical_projection_count": {"type": "integer", "minimum": 0},
        "legacy_projection_count": {"type": "integer", "minimum": 0},
        "billing_verdict": {
            "type": "string",
            "enum": ["not_applicable", "safe_replay_rejected", "ambiguous"],
        },
        "transport_verdict": {
            "type": "string",
            "enum": [
                "not_applicable",
                "provider_free_projection_recovery_available",
                "residual_provider_outcome_unknown",
                "residual_archive_missing",
            ],
        },
        "projection_recovery_allowed": {"type": "boolean"},
    }
)
MAYAK_PARITY_DIAGNOSTIC_SCHEMA = _object(
    {
        "exact_join": {"type": "boolean"},
        "mayak_turn_ref": {
            "anyOf": [
                {"type": "string", "minLength": 16, "maxLength": 16},
                {"type": "null"},
            ]
        },
        "mayak_disposition": {
            "type": "string",
            "enum": ["processed", "unknown"],
        },
        "effective_route": {
            "anyOf": [
                {"type": "string", "enum": ["agent_turn"]},
                {"type": "null"},
            ]
        },
        "reply_task_expected": {
            "anyOf": [
                {"type": "integer", "minimum": 0, "maximum": 1},
                {"type": "null"},
            ]
        },
        "observed_reply_task_count": {"type": "integer", "minimum": 0},
        "parity_policy_version": {"type": "integer", "minimum": 3},
        "classification": {
            "type": "string",
            "enum": [
                "expected_zero_reply_task",
                "unexpected_reply_task",
                "ambiguous",
            ],
        },
        "historical_false_positive": {"type": "boolean"},
    }
)
HISTORICAL_PARITY_DEBT_RECORD_SCHEMA = _object(
    {
        "evidence_id": {"type": "string", "format": "uuid"},
        "canonical_message_id": {"type": "string", "format": "uuid"},
        "legacy_message_id": {
            "anyOf": [
                {"type": "string", "format": "uuid"},
                {"type": "null"},
            ]
        },
        "record_ref": {"type": "string", "minLength": 16, "maxLength": 16},
        "first_observed_at": {"type": "string", "format": "date-time"},
        "last_observed_at": {"type": "string", "format": "date-time"},
        "observation_count": {"type": "integer", "minimum": 1},
        "canonical_message_exists": {"type": "boolean", "const": True},
        "legacy_message_count": {"type": "integer", "minimum": 0},
        "reply_task_count": {"type": "integer", "minimum": 0},
        "reply_task_state": {
            "type": "string",
            "enum": ["absent", "present", "duplicated"],
        },
        "trigger_matches_message": {"type": "boolean"},
        "account_scope_matched": {"type": "boolean"},
        "project_scope_matched": {"type": "boolean"},
        "chat_scope_matched": {"type": "boolean"},
        "business_connection_scope_matched": {"type": "boolean"},
        "text_matched": {"type": "boolean"},
        "verified_identity_count": {"type": "integer", "minimum": 0},
        "person_identity_confirmed": {"type": "boolean"},
        "message_should_create_reply_task": {
            "anyOf": [{"type": "boolean"}, {"type": "null"}]
        },
        "classification": {
            "type": "string",
            "enum": [
                "real_defect_missing_reply_task",
                "historical_residue_repaired",
                "real_defect_unexpected_reply_task",
                "ambiguous",
            ],
        },
        "mayak_parity": MAYAK_PARITY_DIAGNOSTIC_SCHEMA,
    }
)
HISTORICAL_DEBT_SCHEMA = _object(
    {
        "voice_count": {"type": "integer", "minimum": 0},
        "parity_count": {"type": "integer", "minimum": 0},
        "voice_records": {
            "type": "array",
            "maxItems": 2,
            "items": HISTORICAL_VOICE_DEBT_RECORD_SCHEMA,
        },
        "parity_records": {
            "type": "array",
            "maxItems": 4,
            "items": HISTORICAL_PARITY_DEBT_RECORD_SCHEMA,
        },
        "private_content_exposed": {"type": "boolean", "const": False},
        "provider_identifiers_exposed": {"type": "boolean", "const": False},
        "billing_identifiers_exposed": {"type": "boolean", "const": False},
        "mutation_allowed": {"type": "boolean", "const": False},
        "provider_call_allowed": {"type": "boolean", "const": False},
    }
)
BECOME_SLICE3_STATUS_SCHEMA = _object(
    {
        "schema": {
            "type": "string",
            "const": "gate.become-pilot-slice3-status@1",
        },
        "candidate_count": {"type": "integer", "minimum": 0},
        "slot_count": {"type": "integer", "minimum": 0},
        "invitation_count": {"type": "integer", "minimum": 0},
        "accepted_count": {"type": "integer", "minimum": 0},
        "expiry_due_count": {"type": "integer", "minimum": 0},
        "acceptance_count": {"type": "integer", "minimum": 0},
        "delivery_unknown_count": {"type": "integer", "minimum": 0},
        "relations_unknown_count": {"type": "integer", "minimum": 0},
        "relations_executed_count": {"type": "integer", "minimum": 0},
        "candidate_tombstone_count": {"type": "integer", "minimum": 0},
        "content_exposed": {"type": "boolean", "const": False},
        "provider_identifiers_exposed": {"type": "boolean", "const": False},
    }
)
BECOME_SLICE3_ROLLBACK_SCHEMA = _object(
    {
        "schema": {
            "type": "string",
            "const": "gate.become-pilot-slice3-rollback-preflight@1",
        },
        "accepted_count": {"type": "integer", "minimum": 0},
        "unresolved_delivery_count": {"type": "integer", "minimum": 0},
        "unresolved_relations_count": {"type": "integer", "minimum": 0},
        "schema_downgrade_allowed": {"type": "boolean"},
        "application_rollback_allowed": {"type": "boolean"},
        "content_exposed": {"type": "boolean", "const": False},
    }
)
BECOME_SLICE3_COUNTS_SCHEMA = _object(
    {
        "candidate_total_count": {"type": "integer", "minimum": 0},
        "candidate_count": {"type": "integer", "minimum": 0},
        "slot_count": {"type": "integer", "minimum": 0},
        "invitation_count": {"type": "integer", "minimum": 0},
        "accepted_count": {"type": "integer", "minimum": 0},
        "expiry_due_count": {"type": "integer", "minimum": 0},
        "acceptance_count": {"type": "integer", "minimum": 0},
        "delivery_count": {"type": "integer", "minimum": 0},
        "delivery_reserved_count": {"type": "integer", "minimum": 0},
        "delivery_unknown_count": {"type": "integer", "minimum": 0},
        "outbox_count": {"type": "integer", "minimum": 0},
        "outbox_reserved_count": {"type": "integer", "minimum": 0},
        "relations_unknown_count": {"type": "integer", "minimum": 0},
        "relations_executed_count": {"type": "integer", "minimum": 0},
        "candidate_tombstone_count": {"type": "integer", "minimum": 0},
    }
)
BECOME_SLICE3_RELATIONS_HEALTH_SCHEMA = _object(
    {
        "ready": {"type": "boolean"},
        "error_code": _nullable("string"),
        "source_sha": {
            "anyOf": [
                {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                {"type": "null"},
            ]
        },
        "execution_count": {
            "anyOf": [
                {"type": "integer", "minimum": 0},
                {"type": "null"},
            ]
        },
        "provider_calls": {
            "anyOf": [
                {"type": "integer", "minimum": 0},
                {"type": "null"},
            ]
        },
        "external_effects": {
            "anyOf": [
                {"type": "integer", "minimum": 0},
                {"type": "null"},
            ]
        },
    }
)
BECOME_SLICE3_PRODUCTION_EVIDENCE_SCHEMA = _object(
    {
        "schema": {
            "type": "string",
            "const": "gate.become-pilot-slice3-production-evidence@1",
        },
        "ready": {"type": "boolean"},
        "error_code": _nullable("string"),
        "source_sha": {"type": "string", "minLength": 1, "maxLength": 64},
        "expected_alembic_head": {
            "type": "string",
            "const": "0087_become_pilot_slice3_learner",
        },
        "alembic_head": _nullable("string"),
        "gates": _object(
            {
                "runtime_enabled": {"type": "boolean"},
                "data_gate_approved": {"type": "boolean"},
                "delivery_enabled": {"type": "boolean"},
                "relations_enabled": {"type": "boolean"},
            }
        ),
        "caps": _object(
            {
                "telegram_sends": {"type": "integer", "minimum": 0, "maximum": 2},
                "relations_posts": {"type": "integer", "minimum": 0, "maximum": 1},
            }
        ),
        "slice3_status": {
            "anyOf": [BECOME_SLICE3_STATUS_SCHEMA, {"type": "null"}]
        },
        "rollback_preflight": {
            "anyOf": [BECOME_SLICE3_ROLLBACK_SCHEMA, {"type": "null"}]
        },
        "counts": {
            "anyOf": [BECOME_SLICE3_COUNTS_SCHEMA, {"type": "null"}]
        },
        "relations": BECOME_SLICE3_RELATIONS_HEALTH_SCHEMA,
        "private_content_exposed": {"type": "boolean", "const": False},
        "provider_identifiers_exposed": {"type": "boolean", "const": False},
        "telegram_identifiers_exposed": {"type": "boolean", "const": False},
        "mutation_allowed": {"type": "boolean", "const": False},
    }
)


# Optional for older fixtures, always present in the live status response.
GATE_STATUS_OUTPUT_SCHEMA["properties"]["personal_telegram"] = _object(
    {
        "configured": _nullable("boolean"),
        "authenticated": _nullable("boolean"),
        "runtime_connected": {"type": "boolean"},
        "send_enabled": {"type": "boolean"},
        "create_group_enabled": {"type": "boolean"},
        "last_successful_check": _nullable("string"),
        "error_code": _nullable("string"),
    }
)

# Optional for older fixtures, always present in the live gate_get_status response.
GATE_STATUS_OUTPUT_SCHEMA["properties"]["dropbox_calls"] = _object(
    {
        "scope_allowed": {"type": "boolean"},
        "enabled": {"type": "boolean"},
        "configuration": {
            "type": "object",
            "properties": {
                name: {"type": "boolean"}
                for name in (
                    "app_key", "app_secret", "refresh_token", "account_id",
                    "openai_key", "inbox_path_valid",
                )
            },
            "additionalProperties": False,
        },
        "inbox": {
            "anyOf": [
                {"type": "null"},
                _object(
                    {
                        "cursor_present": {"type": "boolean"},
                        "backfill_version": {"type": "integer"},
                        "pending": {"type": "boolean"},
                        "attempt_count": {"type": "integer"},
                        "next_attempt_at": _nullable("string"),
                        "last_error_code": _nullable("string"),
                        "webhook_received_at": _nullable("string"),
                        "state_updated_at": _nullable("string"),
                    }
                ),
            ]
        },
        "recent_recordings": {
            "type": "array",
            "maxItems": 20,
            "items": _object(
                {
                    "recording_id": {"type": "string"},
                    "source_revision_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "size_bytes": {"type": "integer"},
                    "received_at": _nullable("string"),
                    "created_at": _nullable("string"),
                    "transcription_status": {"type": "string"},
                    "transcription_reason_code": _nullable("string"),
                    "transcribed_at": _nullable("string"),
                    "transcript_present": {"type": "boolean"},
                    "summary_status": _nullable("string"),
                }
            ),
        },
        "record_limit": {"const": 20},
        "private_content_exposed": {"const": False},
        "provider_calls": {"const": 0},
        "mutation_allowed": {"const": False},
    }
)
_dropbox_status_properties = GATE_STATUS_OUTPUT_SCHEMA["properties"]["dropbox_calls"]["properties"]
_dropbox_status_properties.update({
    "no_speech_count": {"type": "integer", "minimum": 0},
    "requires_attention_count": {"type": "integer", "minimum": 0},
    "incomplete_recordings": {
        **_dropbox_status_properties["recent_recordings"], "maxItems": 100,
    },
    "incomplete_truncated": {"type": "boolean"},
    "archive_counts": _object({
        name: {"type": "integer", "minimum": 0}
        for name in ("total", "completed", "superseded", "incomplete")
    }),
})
GATE_STATUS_OUTPUT_SCHEMA["properties"]["historical_debt"] = HISTORICAL_DEBT_SCHEMA
GATE_STATUS_OUTPUT_SCHEMA["properties"]["telegram_voice_archives"] = _object({
    **{name: {"type": "integer", "minimum": 0}
       for name in ("total", "completed", "incomplete")},
    "truncated": {"type": "boolean"},
    "private_content_exposed": {"const": False},
    "incomplete_recordings": {"type": "array", "maxItems": 100, "items": _object({
        "archive_id": {"type": "string"}, "received_at": {"type": "string"},
        "status": _nullable("string"), "reason_code": _nullable("string"),
        "duration_seconds": {"type": "integer"}, "size_bytes": {"type": "integer"},
        "summary_status": _nullable("string"),
    })},
})
GATE_STATUS_OUTPUT_SCHEMA["properties"]["telegram_voice_archives"]["properties"].update({
    "historical_short_voice_overflow_count": {"type": "integer", "minimum": 0},
    "no_speech_count": {"type": "integer", "minimum": 0},
    "requires_attention_count": {"type": "integer", "minimum": 0},
})
GATE_STATUS_OUTPUT_SCHEMA["properties"]["become_slice3"] = (
    BECOME_SLICE3_PRODUCTION_EVIDENCE_SCHEMA
)


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
            "items": _ref("reply_context_message"),
        },
        "count": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "next_cursor": _nullable("string"),
        "has_more": {"type": "boolean"},
    }
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
NULLABLE_FORMATTING_PREVIEW_SCHEMA = {"anyOf": [FORMATTING_PREVIEW_SCHEMA, {"type": "null"}]}


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
            "media_type": {"type": "string", "enum": ["application/pdf", "application/zip"]},
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
            "media_type": {"type": "string", "enum": ["application/pdf", "application/zip"]},
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

GATE_STATUS_OUTPUT_SCHEMA["properties"]["watch_voice"] = _object({
    "enabled": {"type": "boolean"},
    "configured": {"type": "boolean"},
    "scope_allowed": {"type": "boolean"},
    "delivery_counts": {"type": "object", "additionalProperties": {"type": "integer"}},
    "transcription_counts": {"type": "object", "additionalProperties": {"type": "integer"}},
    "private_content_exposed": {"type": "boolean", "enum": [False]},
})
