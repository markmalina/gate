"""JSON output schema for the Gate MCP status tool."""

from app.api.mcp_schema_common import ACCOUNT_SCHEMA, JsonObject, _nullable, _object, _ref

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


GATE_STATUS_OUTPUT_SCHEMA["properties"]["watch_voice"] = _object({
    "enabled": {"type": "boolean"},
    "configured": {"type": "boolean"},
    "scope_allowed": {"type": "boolean"},
    "delivery_counts": {"type": "object", "additionalProperties": {"type": "integer"}},
    "transcription_counts": {"type": "object", "additionalProperties": {"type": "integer"}},
    "private_content_exposed": {"type": "boolean", "enum": [False]},
})


GATE_STATUS_OUTPUT_SCHEMA["properties"]["watch_voice_actions"] = _object({
    "enabled": {"type": "boolean"},
    "workflow_pin_configured": {"type": "boolean"},
    "ordinary_worker_enabled": {"type": "boolean"},
    "scope_allowed": {"type": "boolean"},
    "run_claim_count": {"type": "integer", "minimum": 0},
    "delivery_counts": {"type": "object", "additionalProperties": {"type": "integer"}},
    "transcription_counts": {"type": "object", "additionalProperties": {"type": "integer"}},
    "recovery_counts": {"type": "object", "additionalProperties": {"type": "integer"}},
    "private_content_exposed": {"type": "boolean", "enum": [False]},
})
