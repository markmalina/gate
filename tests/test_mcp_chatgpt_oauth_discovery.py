# ruff: noqa
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db_session
from app.api.routes import get_telegram_client
from app.application.reply_drafts import SavedReplyDraft
from app.core.config import get_settings
from app.core.mcp_security import AccessScope, Principal
from app.main import app


class UnexpectedTelegramClient:
    """Fail if a read-only MCP test reaches the Telegram provider."""

    async def send_message(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("read-only MCP test attempted Telegram send")

    async def get_business_connection(self, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("read-only MCP test queried Telegram")


class NoQuerySession:
    async def execute(self, _statement: object) -> None:
        raise AssertionError("OAuth discovery must not query Gate data")


class CountSession:
    def __init__(self) -> None:
        self.query_count = 0

    async def execute(self, _statement: object) -> Any:
        self.query_count += 1

        class Result:
            def scalar_one(self) -> int:
                return 0

            def all(self) -> list[object]:
                return []

        return Result()

    async def scalar(self, _statement: object) -> int:
        self.query_count += 1
        return 0


def _voice_status_payload() -> dict[str, Any]:
    return {
        "state": "disabled",
        "enabled": False,
        "api_key_configured": False,
        "model": "gpt-4o-mini-transcribe",
        "entitlement_active": True,
        "period_key": "2026-07",
        "hard_limit_microusd": 1_000_000,
        "settled_microusd": 0,
        "reserved_microusd": 0,
        "remaining_microusd": 1_000_000,
        "settled_count": 0,
        "open_count": 0,
        "unknown_count": 0,
        "reconciliation_required": False,
        "received_count": 3,
        "processing_count": 0,
        "pending_count": 0,
        "active_processing_count": 0,
        "stale_processing_count": 0,
        "nonterminal_archive_missing_count": 0,
        "nonterminal_archive_pending_count": 0,
        "nonterminal_archive_transcribed_count": 0,
        "nonterminal_archive_other_count": 0,
        "nonterminal_transcript_complete_count": 0,
        "nonterminal_summary_complete_count": 0,
        "transport_unknown_archive_missing_count": 1,
        "transport_unknown_archive_present_count": 1,
        "transport_unknown_transcript_complete_count": 1,
        "transport_unknown_summary_complete_count": 0,
        "delivered_count": 1,
        "retry_count": 0,
        "dead_letter_count": 2,
        "last_error_code": "openai_transcription_rejected",
        "telegram_delivery_allowed": False,
    }


class CommitSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def oauth_discovery_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GATE_MCP_ENABLED", "true")
    monkeypatch.setenv("MCP_AUTH_MODE", "oauth")
    monkeypatch.setenv("MCP_AUTHORIZATION_SERVER", "https://auth.gate.example")
    monkeypatch.setenv("MCP_PUBLIC_RESOURCE_URL", "https://gate.example/mcp")
    monkeypatch.setenv("MCP_OAUTH_JWKS_URL", "https://auth.gate.example/.well-known/jwks.json")
    get_settings.cache_clear()

    async def override_session() -> AsyncIterator[NoQuerySession]:
        yield NoQuerySession()

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_telegram_client] = lambda: UnexpectedTelegramClient()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


async def _post(payload: dict[str, Any], authorization: str | None = None):
    headers = {"Authorization": authorization} if authorization else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        return await client.post("/mcp", json=payload, headers=headers)


async def _get(path: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gate.example") as client:
        return await client.get(path)


@pytest.mark.anyio
async def test_oauth_metadata_advertises_only_active_scopes_and_tools() -> None:
    response = await _get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scopes_supported"] == [
        "gate:status:read",
        "gate:inbox:read",
        "gate:drafts:write",
        "gate:messages:send",
        "gate:agents:manage",
    ]
    assert payload["mcp"]["read_only_tools"] == [
        "gate_get_status",
        "gate_list_unanswered",
        "gate_get_reply_context",
        "gate_alpha_list_installations",
        "gate_alpha_get_webhook_status",
        "gate_alpha_list_shadow_reviews",
        "gate_alpha_verify_review_replay",
        "gate_beta_export_space_checkpoint",
    ]
    assert payload["mcp"]["write_tools"] == [
        "gate_save_reply_draft",
        "gate_alpha_control_installation",
        "gate_alpha_run_model_preflight",
        "gate_alpha_get_test_group_presence",
        "gate_beta_get_provider_probe",
    ]
    assert payload["mcp"]["send_tools"] == [
        "gate_send_approved_reply",
        "gate_queue_approved_reply_sequence",
        "gate_alpha_send_review",
    ]
    assert payload["mcp"]["send_requires_explicit_confirmation"] is True


@pytest.mark.anyio
async def test_chatgpt_can_initialize_and_scan_tools_before_oauth() -> None:
    initialize = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    tools_response = await _post(
        {"jsonrpc": "2.0", "id": "tools", "method": "tools/list"}
    )

    assert initialize.status_code == 200
    tools = tools_response.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "gate_get_status",
        "gate_list_unanswered",
        "gate_get_reply_context",
        "gate_save_reply_draft",
        "gate_send_approved_reply",
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
    ]
    assert tools[0]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:status:read"]}
    ]
    assert tools[0]["_meta"]["securitySchemes"] == tools[0]["securitySchemes"]
    assert tools[1]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:inbox:read"]}
    ]
    assert tools[2]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:inbox:read"]}
    ]
    assert tools[3]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:drafts:write"]}
    ]
    assert tools[4]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:messages:send"]}
    ]
    assert tools[5]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:messages:send"]}
    ]
    assert tools[6]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:status:read"]}
    ]
    assert tools[7]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:status:read"]}
    ]
    assert tools[8]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:agents:manage"]}
    ]
    assert tools[9]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:agents:manage"]}
    ]
    assert tools[10]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["gate:agents:manage"]}
    ]
    assert tools[3]["annotations"]["destructiveHint"] is False
    assert "never approves or sends" in tools[3]["description"]
    assert tools[4]["annotations"]["idempotentHint"] is True
    assert tools[4]["annotations"]["openWorldHint"] is True


@pytest.mark.anyio
async def test_tool_call_returns_chatgpt_oauth_challenge_without_data() -> None:
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "oauth-required",
            "method": "tools/call",
            "params": {"name": "gate_list_unanswered", "arguments": {}},
        }
    )

    result = response.json()["result"]
    assert result["isError"] is True
    challenge = result["_meta"]["mcp/www_authenticate"][0]
    assert (
        'resource_metadata="https://gate.example/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    assert 'scope="gate:inbox:read"' in challenge
    assert 'error="invalid_token"' in challenge
    assert "error_description=" in challenge


@pytest.mark.anyio
async def test_verified_oauth_principal_can_call_scoped_read_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = CountSession()

    async def override_session() -> AsyncIterator[CountSession]:
        yield session

    async def authenticate(**kwargs: Any) -> Principal:
        assert kwargs["authorization"] == "Bearer signed-token"
        assert kwargs["required_scope"] == "gate:status:read"
        return Principal(
            subject="user-123",
            auth_method="oauth2",
            scopes=frozenset({"gate:status:read"}),
            access_scope=AccessScope(
                workspace_id="mark-personal",
                account_ids=frozenset({UUID("22222222-2222-2222-2222-222222222222")}),
                project_ids=frozenset({UUID("33333333-3333-3333-3333-333333333333")}),
            ),
        )

    async def voice_status(_session: object, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(payload=_voice_status_payload)

    async def parity_status(_session: object, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            payload=lambda: {
                "state": "no_evidence",
                "total_count": 0,
                "matched_count": 0,
                "pending_count": 0,
                "mismatch_count": 0,
                "required_account_count": 0,
                "covered_account_count": 0,
                "mismatch_codes": [],
                "cutover_ready": False,
                "private_content_exposed": False,
            }
        )

    async def become_intake_status(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return {
            "schema": "gate.become-controlled-intake@1",
            "receipt_count": 0,
            "registration_confirmed_count": 0,
            "owner_only_count": 0,
            "registration_ready": False,
            "consent_accepted_count": 0,
            "consent_ready": False,
            "relations_handoff_count": 0,
            "relations_handoff_ready": False,
            "relations_delivery_enabled": False,
            "relations_delivered_count": 0,
            "relations_delivery_ready": False,
            "relations_delivery_attempts": 0,
            "relations_consent_executed_count": 0,
            "relations_consent_executed_ready": False,
            "media_prompt_gate_ready": False,
            "media_prompt_count": 0,
            "media_prompt_sent_count": 0,
            "media_prompt_unknown_count": 0,
            "media_prompt_failed_count": 0,
            "media_prompt_ready": False,
            "media_prompt_reply_required": False,
            "media_prompt_reconciliation_required": False,
            "media_intake_gate_ready": False,
            "media_intake_ready": False,
            "media_intake_count": 0,
            "media_pending_download_count": 0,
            "voice_intake_count": 0,
            "video_intake_count": 0,
            "voice_correlation_count": 0,
            "voice_correlation_ready": False,
            "chain_slot_count": 0,
            "chain_slots_initialized_ready": False,
            "chain_execution_enabled": False,
            "chain_ready_count": 0,
            "chain_requested_count": 0,
            "chain_unknown_count": 0,
            "chain_reconciliation_required": False,
            "relations_chain_completed_count": 0,
            "mayaki_chain_completed_count": 0,
            "oko_chain_completed_count": 0,
            "take_chain_completed_count": 0,
            "free_result_delivery_enabled": False,
            "controlled_test_payment_enabled": False,
            "raw_media_retention_days": 7,
            "raw_media_retained_count": 0,
            "raw_media_purged_count": 0,
            "raw_media_retained_bytes": 0,
            "confirmed_transcript_retention_days": 30,
            "confirmed_transcript_retained_count": 0,
            "confirmed_transcript_purged_count": 0,
            "external_effects": 0,
            "provider_calls": 0,
            "messages_or_publications": 0,
            "media_bytes": 0,
            "real_payments": 0,
            "private_content_exposed": False,
            "provider_identifiers_exposed": False,
            "telegram_identifiers_exposed": False,
        }

    async def recovery_status(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return {
            "chain_recovery_receipt_count": 0,
            "chain_recovery_prepared_count": 0,
            "chain_recovery_prepared_receipt_enabled": False,
            "chain_recovery_enabled": False,
            "chain_recovery_activation_max_posts": 0,
            "chain_recovery_attempt_version": None,
            "chain_recovery_target_source_sha": None,
            "chain_recovery_source_failure_preserved": False,
            "chain_recovery_attempt_count": 0,
            "chain_recovery_activation_receipt_count": 0,
            "chain_recovery_attempt_digest": None,
            "chain_recovery_prepared_digest": None,
            "chain_recovery_attempt_state": "prepared",
            "chain_recovery_post_count": 0,
            "chain_recovery_reconciliation_required": False,
            "chain_recovery_mayaki_source_sha": (
                "2078d230852af1c7e3a309f00d716fa2fc664f68"
            ),
            "chain_recovery_oko_source_sha": (
                "2c304529d04be43726e677d2b864c4b5f8283be8"
            ),
            "chain_recovery_take_source_sha": (
                "4a81bf8e221929be8b8898ddd52a0cbdbbe2e9dc"
            ),
            "chain_recovery_take_receipt_digest": None,
            "chain_recovery_mayaki_state": "not_started",
            "chain_recovery_mayaki_post_count": 0,
            "chain_recovery_mayaki_request_digest": None,
            "chain_recovery_mayaki_receipt_digest": None,
            "chain_recovery_oko_state": "not_started",
            "chain_recovery_oko_post_count": 0,
            "chain_recovery_oko_request_digest": None,
            "chain_recovery_oko_receipt_digest": None,
            "chain_recovery_take_state": "not_started",
            "chain_recovery_take_post_count": 0,
            "chain_recovery_take_request_digest": None,
        }

    async def quest_status(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return {
            "quest_projection_enabled": False,
            "quest_activation_max_operations": 0,
            "quest_role_gate_enabled": False,
            "quest_approval_gate_enabled": False,
            "quest_count": 0,
            "quest_participant_count": 0,
            "quest_approval_count": 0,
            "quest_enrollment_count": 0,
            "quest_attempt_count": 0,
            "quest_reconciliation_required": False,
            "quest_external_effects": 0,
        }

    async def historical_debt_status(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return {
            "voice_count": 0,
            "parity_count": 0,
            "voice_records": [],
            "parity_records": [],
            "private_content_exposed": False,
            "provider_identifiers_exposed": False,
            "billing_identifiers_exposed": False,
            "mutation_allowed": False,
            "provider_call_allowed": False,
        }


    async def beta_katya_status(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return {
            "state": "configuration_incomplete",
            "gates": {
                "production": False,
                "connector": False,
                "privacy_mode_confirmed": False,
                "space_enabled": False,
                "space_ready": False,
                "model_runtime_enabled": False,
                "model_data_gate_approved": False,
                "delivery_enabled": False,
            },
            "configured": {
                "chat_binding": False,
                "subject_binding": False,
                "consent_digest": False,
            },
            "binding_state": "absent",
            "production_ready": False,
            "event_count": 0,
            "consent_count": 0,
            "fact_count": 0,
            "delivery_sent_count": 0,
            "delivery_unknown_count": 0,
            "private_identifiers_exposed": False,
            "private_payload_exposed": False,
        }


    async def beta_wedding_status(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return {
            "state": "awaiting_binding",
            "gates": {
                "production": True,
                "connector": True,
                "privacy_mode_confirmed": True,
                "space_enabled": False,
                "space_ready": False,
                "delivery_enabled": False,
            },
            "configured": {
                "chat_binding": False,
                "subject_binding": False,
                "activation_digest": False,
            },
            "binding_state": "absent",
            "production_ready": False,
            "event_count": 0,
            "fact_count": 0,
            "reply_sent_count": 0,
            "reply_unknown_count": 0,
            "private_identifiers_exposed": False,
            "private_payload_exposed": False,
        }


    async def connectivity_status(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return {
            "mayaki_connectivity_proven": False,
            "mayaki_connectivity_error_code": "connection_not_established",
            "mayaki_service_name": "become-mayaki-controlled",
            "mayaki_private_hostname": (
                "become-mayaki-controlled.railway.internal"
            ),
            "mayaki_private_port": 3000,
            "mayaki_source_sha": (
                "2078d230852af1c7e3a309f00d716fa2fc664f68"
            ),
            "mayaki_health_external_effects": 0,
            "oko_connectivity_proven": False,
            "oko_connectivity_error_code": "connection_not_established",
            "oko_service_name": "become-oko-controlled",
            "oko_private_hostname": "become-oko-controlled.railway.internal",
            "oko_private_port": 3000,
            "oko_source_sha": (
                "2c304529d04be43726e677d2b864c4b5f8283be8"
            ),
            "oko_health_external_effects": 0,
            "take_connectivity_proven": False,
            "take_connectivity_error_code": "connection_not_established",
            "take_service_name": "become-take-controlled",
            "take_private_hostname": "become-take-controlled.railway.internal",
            "take_private_port": 8000,
            "take_source_sha": (
                "4a81bf8e221929be8b8898ddd52a0cbdbbe2e9dc"
            ),
            "take_health_external_effects": 0,
        }

    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr("app.api.mcp_routes.authenticate_mcp_oauth", authenticate)
    monkeypatch.setattr(
        "app.api.mcp_routes.telegram_voice_operational_status",
        voice_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.telegram_business_text_parity_status",
        parity_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.historical_debt_diagnostics",
        historical_debt_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.become_controlled_intake_status",
        become_intake_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.become_i4_recovery_status",
        recovery_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.become_quest_status",
        quest_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.probe_all_i4_connectivity",
        connectivity_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.beta_katya_space_operational_status",
        beta_katya_status,
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.beta_wedding_space_operational_status",
        beta_wedding_status,
    )
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "oauth-call",
            "method": "tools/call",
            "params": {"name": "gate_get_status", "arguments": {}},
        },
        authorization="Bearer signed-token",
    )

    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["mcp"]["auth"] == "oauth2_enforced"
    assert session.query_count == 33


@pytest.mark.anyio
async def test_verified_oauth_principal_can_save_draft_without_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = CommitSession()
    task_id = UUID("11111111-1111-1111-1111-111111111111")
    account_id = UUID("22222222-2222-2222-2222-222222222222")
    project_id = UUID("33333333-3333-3333-3333-333333333333")

    async def override_session() -> AsyncIterator[CommitSession]:
        yield session

    async def authenticate(**kwargs: Any) -> Principal:
        assert kwargs["required_scope"] == "gate:drafts:write"
        return Principal(
            subject="user-123",
            auth_method="oauth2",
            scopes=frozenset({"gate:drafts:write"}),
            access_scope=AccessScope(
                workspace_id="mark-personal",
                account_ids=frozenset({account_id}),
                project_ids=frozenset({project_id}),
            ),
        )

    class Writer:
        def __init__(self, _session: Any) -> None:
            pass

        async def save(self, command: Any) -> SavedReplyDraft:
            return SavedReplyDraft(
                draft_id=UUID("44444444-4444-4444-4444-444444444444"),
                task_id=command.task_id,
                account_id=account_id,
                project_id=project_id,
                account_alias="telegram_mark_ru",
                account_label="RU",
                recipient_chat_id="12345",
                status="generated",
                text=command.text,
            )

    app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr("app.api.mcp_routes.authenticate_mcp_oauth", authenticate)
    monkeypatch.setattr("app.api.mcp_routes.SqlReplyDraftWriter", Writer)
    response = await _post(
        {
            "jsonrpc": "2.0",
            "id": "draft-call",
            "method": "tools/call",
            "params": {
                "name": "gate_save_reply_draft",
                "arguments": {"task_id": str(task_id), "text": "  Привет!  "},
            },
        },
        authorization="Bearer signed-token",
    )

    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["text"] == "Привет!"
    assert result["structuredContent"]["sent"] is False
    assert result["structuredContent"]["approval_required"] is True
    assert result["structuredContent"]["send_available"] is True
    assert result["structuredContent"]["account"]["label"] == "RU"
    assert result["structuredContent"]["recipient_chat_id"] == "12345"
    assert result["structuredContent"]["confirmation"] == {
        "expected_account": "RU",
        "expected_recipient_chat_id": "12345",
    }
    assert session.commits == 1


@pytest.mark.anyio
async def test_oauth_mode_fails_closed_when_discovery_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_AUTHORIZATION_SERVER", "")
    get_settings.cache_clear()
    response = await _post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 503
    assert response.json()["detail"] == "MCP OAuth validation is not configured"
