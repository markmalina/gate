"""Build and format the scoped status exposed by the Gate MCP API."""

from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.alpha_model_readiness import (
    AlphaModelReadinessFacts,
    alpha_model_readiness_payload,
    alpha_release_binding_readiness,
)
from app.application.mcp_status import JsonObject, build_gate_status
from app.core.config import Settings
from app.core.mcp_security import Principal
from app.infrastructure.alpha_canary import (
    alpha_canary_active_installation_count,
    alpha_canary_audit_counts,
    alpha_canary_binding_count,
    alpha_canary_installation_count,
)
from app.infrastructure.alpha_model_status import alpha_model_operational_status
from app.infrastructure.become_controlled_intake import become_controlled_intake_status
from app.infrastructure.become_i4_connectivity import probe_all_i4_connectivity
from app.infrastructure.become_i4_recovery import become_i4_recovery_status
from app.infrastructure.become_quest import become_quest_status
from app.infrastructure.become_slice3_status import become_slice3_production_evidence
from app.infrastructure.beta_space import beta_katya_space_operational_status
from app.infrastructure.beta_wedding_binding import beta_wedding_space_operational_status
from app.infrastructure.dropbox_call_status import dropbox_call_operational_status
from app.infrastructure.historical_debt_diagnostics import historical_debt_diagnostics
from app.infrastructure.owner_voice_recovery import owner_voice_archive_inventory
from app.infrastructure.telegram_business_text_parity import telegram_business_text_parity_status
from app.infrastructure.telegram_voice_status import telegram_voice_operational_status
from app.infrastructure.universal_mayak_status import universal_mayak_operational_status
from app.infrastructure.watch_voice_actions import operational_status as watch_voice_actions_status
from app.models.domain import UNCLASSIFIED_PROJECT_ID
from app.worker.watch_voice import operational_status as watch_voice_operational_status


async def build_status_snapshot(
    *,
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    protocol_version: str,
    tool_names: list[str],
) -> JsonObject:
    """Collect the existing operational sections within the caller's MCP scope."""
    snapshot = await build_gate_status(
        session=session,
        settings=settings,
        principal=principal,
        protocol_version=protocol_version,
        tool_names=tool_names,
    )
    snapshot["dropbox_calls"] = await dropbox_call_operational_status(
        session,
        workspace_key=principal.access_scope.workspace_id,
        configured_workspace=settings.gate_workspace_id,
        account_id=settings.dropbox_call_account_id,
        project_ids=frozenset(str(value) for value in principal.access_scope.project_ids),
        intake_project_allowed=UNCLASSIFIED_PROJECT_ID in principal.access_scope.project_ids,
        enabled=settings.gate_dropbox_call_intake_enabled,
        configuration={
            "app_key": bool(settings.dropbox_call_app_key),
            "app_secret": bool(settings.dropbox_call_app_secret.get_secret_value()),
            "refresh_token": bool(settings.dropbox_call_refresh_token.get_secret_value()),
            "account_id": bool(settings.dropbox_call_account_id),
            "openai_key": bool(settings.openai_api_key.get_secret_value()),
            "inbox_path_valid": settings.dropbox_call_inbox_path == "/Alpha/Calls",
        },
    )
    if settings.gate_watch_voice_actions_enabled:
        snapshot["watch_voice_actions"] = await watch_voice_actions_status(
            session, settings,
            scope_allowed=(principal.access_scope.workspace_id == settings.gate_workspace_id
                           and UNCLASSIFIED_PROJECT_ID in principal.access_scope.project_ids),
        )
    if settings.gate_watch_voice_enabled:
        snapshot["watch_voice"] = await watch_voice_operational_status(
            session, settings,
            scope_allowed=(principal.access_scope.workspace_id == settings.gate_workspace_id
                           and UNCLASSIFIED_PROJECT_ID in principal.access_scope.project_ids),
        )
    snapshot["alpha_canary"] = await _build_alpha_canary_status(
        session=session,
        settings=settings,
        workspace_key=principal.access_scope.workspace_id,
    )
    snapshot["beta_katya_space"] = await beta_katya_space_operational_status(
        session,
        workspace_key=principal.access_scope.workspace_id,
        settings=settings,
    )
    snapshot["beta_wedding_space"] = (
        await beta_wedding_space_operational_status(
            session,
            workspace_key=principal.access_scope.workspace_id,
            settings=settings,
        )
    )
    snapshot["universal_mayak"] = await universal_mayak_operational_status(
        session,
        workspace_key=principal.access_scope.workspace_id,
        settings=settings,
    )
    become_status = await become_controlled_intake_status(
        session,
        workspace_key=principal.access_scope.workspace_id,
        transcription_enabled=(
            settings.gate_become_voice_transcription_enabled
        ),
        transcription_data_gate_approved=(
            settings.gate_become_voice_transcription_data_gate_approved
        ),
        transcription_max_calls=(
            settings.gate_become_voice_transcription_max_calls
        ),
        transcription_api_key_configured=bool(
            settings.openai_api_key.get_secret_value()
        ),
        chain_execution_enabled=(
            settings.gate_become_i4_chain_execution_enabled
        ),
        now=datetime.now(UTC),
    )
    recovery_status = await become_i4_recovery_status(
        session,
        workspace_key=principal.access_scope.workspace_id,
        execution_enabled=(
            settings.gate_become_i4_recovery_execution_enabled
        ),
        max_posts=settings.gate_become_i4_recovery_max_posts,
    )
    become_status.update(recovery_status)
    become_status.update(
        await become_quest_status(
            session,
            workspace_key=principal.access_scope.workspace_id,
        )
    )
    become_status["chain_reconciliation_required"] = bool(
        become_status["chain_reconciliation_required"]
        or recovery_status["chain_recovery_reconciliation_required"]
    )
    async with httpx.AsyncClient() as connectivity_client:
        become_status.update(
            await probe_all_i4_connectivity(
                connectivity_client,
            )
        )
    snapshot["become_controlled_intake"] = become_status
    snapshot["telegram_voice"] = (
        await telegram_voice_operational_status(
            session,
            workspace_key=principal.access_scope.workspace_id,
            enabled=settings.gate_telegram_voice_transcription_enabled,
            api_key_configured=bool(
                settings.openai_api_key.get_secret_value()
            ),
            model=settings.gate_telegram_voice_transcription_model,
            now=datetime.now(UTC),
        )
    ).payload()
    snapshot["telegram_business_text_parity"] = (
        await telegram_business_text_parity_status(
            session,
            workspace_key=principal.access_scope.workspace_id,
            account_ids=principal.access_scope.account_ids,
        )
    ).payload()
    snapshot["telegram_voice_archives"] = await owner_voice_archive_inventory(
        session,
        workspace_key=principal.access_scope.workspace_id,
        account_ids=principal.access_scope.account_ids,
        project_ids=principal.access_scope.project_ids,
    )
    snapshot["historical_debt"] = await historical_debt_diagnostics(
        session,
        workspace_key=principal.access_scope.workspace_id,
        account_ids=principal.access_scope.account_ids,
        project_ids=principal.access_scope.project_ids,
    )
    async with httpx.AsyncClient() as slice3_status_client:
        snapshot["become_slice3"] = await become_slice3_production_evidence(
            session,
            settings=settings,
            http_client=slice3_status_client,
            now=datetime.now(UTC),
        )
    return snapshot

def format_status_text(snapshot: JsonObject) -> str:
    counts = snapshot["counts"]
    lines = [
        f"Gate status — {snapshot['environment']}",
        f"Commit: {snapshot['commit']}",
        f"Bot: {snapshot['bot_username']}",
        f"Mode: {snapshot['default_mode']}",
        f"Send enabled: {snapshot['send_enabled']}",
        f"Workspace: {snapshot['access']['workspace_id']}",
        "",
        "Telegram Business connections:",
    ]
    for item in snapshot["connection_health"]:
        lines.append(
            f"- {item['account']['label']}: {item['state']}; "
            f"can_reply={item['can_reply']}"
        )
    lines.extend(["", "Contact directory:"])
    for item in snapshot["contact_directory"]:
        source = item["last_source"] or "none"
        last_import_at = item["last_import_at"] or "never"
        lines.append(
            f"- {item['account']['label']}: imported={item['imported_contacts']}; "
            f"source={source}; last_import_at={last_import_at}"
        )
    lines.extend(["", "Counts:"])
    lines.extend(f"- {name}: {value}" for name, value in counts.items())
    alpha = snapshot["alpha_canary"]
    lines.extend(
        [
            "",
            "Alpha canary:",
            f"- connector_enabled: {alpha['connector_enabled']}",
            f"- webhook_credentials_configured: "
            f"{alpha['webhook_credentials_configured']}",
            f"- one_group_discovered: {alpha['one_group_discovered']}",
            f"- one_active_exact_revision: "
            f"{alpha['one_active_exact_revision']}",
            f"- groups_within_capacity: "
            f"{alpha['groups_within_capacity']}",
            f"- all_active_bindings_exact: "
            f"{alpha['all_active_bindings_exact']}",
            f"- reviewed_model_release_ready: "
            f"{alpha['reviewed_model_release_ready']}",
            f"- delivery_enabled: {alpha['delivery_enabled']}",
            f"- accepted_messages: {alpha['audit']['canonical_messages']}",
            f"- agent_turns: {alpha['audit']['agent_turns']}",
            f"- delivery_grants: {alpha['audit']['delivery_grants']}",
            f"- send_attempts: {alpha['audit']['send_attempts']}",
            "",
            "Alpha model shadow:",
            f"- state: {alpha['model_shadow']['state']}",
            f"- staged_deployments: "
            f"{alpha['model_shadow']['staged_deployments']}",
            f"- provider_preflight_verified: "
            f"{alpha['model_shadow']['provider_preflight_verified']}",
            f"- model_turns: {alpha['model_shadow']['model_turns']}",
            f"- failed_turns: {alpha['model_shadow']['failed_turns']}",
            f"- running_turns: {alpha['model_shadow']['running_turns']}",
            f"- model_delivery_grants: "
            f"{alpha['model_shadow']['model_delivery_grants']}",
            f"- stuck_provider_preflights: "
            f"{alpha['model_shadow']['stuck_provider_preflights']}",
            f"- provider_error_category: "
            f"{alpha['model_shadow']['last_provider_error_category']}",
            f"- promotion_next: "
            f"{alpha['model_shadow']['promotion_dry_run']['next_stage']}",
            f"- promotion_blocked_by: "
            f"{','.join(alpha['model_shadow']['promotion_dry_run']['blocked_by']) or 'none'}",
            f"- budget_reserved: $"
            f"{alpha['model_shadow']['budget']['reserved_microusd'] / 1_000_000:.6f}",
            f"- budget_remaining: $"
            f"{alpha['model_shadow']['budget']['remaining_microusd'] / 1_000_000:.6f}",
        ]
    )
    beta_katya = snapshot["beta_katya_space"]
    lines.extend(
        [
            "",
            "Beta — Зубы Кати:",
            f"- state: {beta_katya['state']}",
            f"- production_ready: {beta_katya['production_ready']}",
            f"- binding_state: {beta_katya['binding_state']}",
            f"- consent_count: {beta_katya['consent_count']}",
            f"- fact_count: {beta_katya['fact_count']}",
            f"- delivery_sent_count: {beta_katya['delivery_sent_count']}",
            f"- delivery_unknown_count: {beta_katya['delivery_unknown_count']}",
        ]
    )
    beta_wedding = snapshot["beta_wedding_space"]
    lines.extend(
        [
            "",
            "Beta — Свадьба в Минске:",
            f"- state: {beta_wedding['state']}",
            f"- production_ready: {beta_wedding['production_ready']}",
            f"- binding_state: {beta_wedding['binding_state']}",
            f"- fact_count: {beta_wedding['fact_count']}",
            f"- reply_sent_count: {beta_wedding['reply_sent_count']}",
            f"- reply_unknown_count: {beta_wedding['reply_unknown_count']}",
            f"- delivery_enabled: {beta_wedding['gates']['delivery_enabled']}",
        ]
    )
    voice = snapshot["telegram_voice"]
    lines.extend(
        [
            "",
            "Telegram voice transcription:",
            f"- state: {voice['state']}",
            f"- enabled: {voice['enabled']}",
            f"- api_key_configured: {voice['api_key_configured']}",
            f"- model: {voice['model']}",
            f"- entitlement_active: {voice['entitlement_active']}",
            f"- settled_count: {voice['settled_count']}",
            f"- open_count: {voice['open_count']}",
            f"- unknown_count: {voice['unknown_count']}",
            f"- reconciliation_required: "
            f"{voice['reconciliation_required']}",
            f"- received_count: {voice['received_count']}",
            f"- processing_count: {voice['processing_count']}",
            f"- pending_count: {voice['pending_count']}",
            f"- active_processing_count: {voice['active_processing_count']}",
            f"- stale_processing_count: {voice['stale_processing_count']}",
            f"- nonterminal_archive_missing_count: "
            f"{voice['nonterminal_archive_missing_count']}",
            f"- nonterminal_archive_pending_count: "
            f"{voice['nonterminal_archive_pending_count']}",
            f"- nonterminal_archive_transcribed_count: "
            f"{voice['nonterminal_archive_transcribed_count']}",
            f"- nonterminal_archive_other_count: "
            f"{voice['nonterminal_archive_other_count']}",
            f"- nonterminal_transcript_complete_count: "
            f"{voice['nonterminal_transcript_complete_count']}",
            f"- nonterminal_summary_complete_count: "
            f"{voice['nonterminal_summary_complete_count']}",
            f"- transport_unknown_archive_missing_count: "
            f"{voice['transport_unknown_archive_missing_count']}",
            f"- transport_unknown_archive_present_count: "
            f"{voice['transport_unknown_archive_present_count']}",
            f"- transport_unknown_transcript_complete_count: "
            f"{voice['transport_unknown_transcript_complete_count']}",
            f"- transport_unknown_summary_complete_count: "
            f"{voice['transport_unknown_summary_complete_count']}",
            f"- delivered_count: {voice['delivered_count']}",
            f"- retry_count: {voice['retry_count']}",
            f"- dead_letter_count: {voice['dead_letter_count']}",
            f"- dead_letter_by_code: {voice.get('dead_letter_by_code', {})}",
            f"- last_error_code: {voice['last_error_code'] or 'none'}",
            f"- budget_settled: $"
            f"{voice['settled_microusd'] / 1_000_000:.6f}",
            f"- budget_reserved: $"
            f"{voice['reserved_microusd'] / 1_000_000:.6f}",
            f"- budget_remaining: $"
            f"{voice['remaining_microusd'] / 1_000_000:.6f}",
            f"- telegram_delivery_allowed: "
            f"{voice['telegram_delivery_allowed']}",
        ]
    )
    parity = snapshot["telegram_business_text_parity"]
    lines.extend(
        [
            "",
            "Telegram Business text parity:",
            f"- state: {parity['state']}",
            f"- matched_count: {parity['matched_count']}",
            f"- pending_count: {parity['pending_count']}",
            f"- mismatch_count: {parity['mismatch_count']}",
            f"- covered_accounts: {parity['covered_account_count']}/"
            f"{parity['required_account_count']}",
            f"- cutover_ready: {parity['cutover_ready']}",
            "- private_content_exposed: False",
        ]
    )
    return "\n".join(lines)


async def _build_alpha_canary_status(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_key: str,
) -> JsonObject:
    installation_count = await alpha_canary_installation_count(
        session,
        workspace_key=workspace_key,
    )
    active_installation_count = await alpha_canary_active_installation_count(
        session,
        workspace_key=workspace_key,
    )
    active_binding_count = await alpha_canary_binding_count(
        session,
        workspace_key=workspace_key,
    )
    audit_counts = await alpha_canary_audit_counts(
        session,
        workspace_key=workspace_key,
    )
    model = await alpha_model_operational_status(
        session,
        workspace_key=workspace_key,
    )
    binding_readiness = alpha_release_binding_readiness(
        discovered_installations=installation_count,
        group_capacity=settings.gate_alpha_group_capacity,
        active_installations=active_installation_count,
        exact_active_bindings=active_binding_count,
        reviewed_model_bindings=model.active_model_bindings,
    )
    model_shadow = alpha_model_readiness_payload(
        AlphaModelReadinessFacts(
            runtime_enabled=settings.gate_alpha_model_runtime_enabled,
            data_gate_approved=settings.gate_alpha_model_data_gate_approved,
            api_key_configured=bool(settings.openai_api_key.get_secret_value()),
            staged_deployments=model.staged_deployments,
            monthly_budget_microusd=model.monthly_budget_microusd,
            reserved_microusd=model.reserved_microusd,
            model_turns=model.model_turns,
            proposed_turns=model.proposed_turns,
            failed_turns=model.failed_turns,
            queued_turns=model.queued_turns,
            running_turns=model.running_turns,
            model_delivery_grants=model.model_delivery_grants,
            stuck_provider_preflights=model.stuck_provider_preflights,
            last_provider_error_code=model.last_provider_error_code,
            fixture_binding_active=(
                binding_readiness["all_active_bindings_exact"]
                and model.active_model_bindings == 0
            ),
            telegram_delivery_blocked=not (
                settings.gate_alpha_delivery_enabled
                and binding_readiness["reviewed_model_release_ready"]
            ),
            provider_preflight_successes=model.provider_preflight_successes,
            provider_preflight_failures=model.provider_preflight_failures,
        )
    )
    return {
        "telegram_worker_enabled": settings.gate_telegram_worker_enabled,
        "connector_enabled": settings.gate_alpha_connector_enabled,
        "shadow_worker_enabled": settings.gate_alpha_shadow_worker_enabled,
        "canary_enabled": settings.gate_alpha_canary_enabled,
        "delivery_enabled": settings.gate_alpha_delivery_enabled,
        "privacy_mode_confirmed": settings.gate_alpha_privacy_mode_confirmed,
        "webhook_credentials_configured": bool(
            settings.telegram_alpha_bot_token.get_secret_value()
            and settings.telegram_alpha_bot_username
            and settings.telegram_alpha_webhook_secret.get_secret_value()
        ),
        "one_group_discovered": installation_count == 1,
        "one_active_exact_revision": active_binding_count == 1,
        "groups_within_capacity": binding_readiness["groups_within_capacity"],
        "all_active_bindings_exact": binding_readiness[
            "all_active_bindings_exact"
        ],
        "reviewed_model_release_ready": binding_readiness[
            "reviewed_model_release_ready"
        ],
        "audit": audit_counts,
        "model_shadow": model_shadow,
    }
