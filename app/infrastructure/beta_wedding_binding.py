"""Read-only operational status for the pre-provisioned Wedding Beta Space."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.beta_material import BetaMaterialDeliveryModel, BetaMaterialJobModel
from app.models.beta_space import (
    BetaCheckpointDispatchIntentModel,
    BetaSpaceBindingModel,
    BetaSpaceEventModel,
    BetaSpaceReplyModel,
    BetaSpaceWorkItemModel,
)
from app.models.canonical import Workspace

from .beta_space import beta_space_production_ready

BETA_WEDDING_SPACE_REF = "spc_fcdd8e57a7e832840afcc187fb1d4b2c"
BETA_WEDDING_TENANT_REF = "beta-user-mark-wedding-001"


def wedding_binding_is_verified(binding: BetaSpaceBindingModel | None) -> bool:
    """Require stored policy plus authenticated, immutable control-plane evidence."""

    return bool(
        binding is not None
        and binding.state == "consented"
        and binding.actor_policy == "group_members"
        and binding.provenance_state == "verified"
        and binding.provenance_evidence_kind == "authenticated_control_plane"
    )


def _terminal_error_category(error_code: str) -> str:
    """Collapse an internal error code into a non-sensitive operator category."""

    normalized = error_code.casefold()
    if any(token in normalized for token in ("timeout", "rate", "unavailable", "retry")):
        return "external_transient"
    if any(token in normalized for token in ("telegram", "delivery", "send")):
        return "transport"
    if any(token in normalized for token in ("provider", "model", "transcript")):
        return "provider"
    if any(token in normalized for token in ("invalid", "schema", "contract", "validation")):
        return "validation"
    return "internal"


async def beta_wedding_space_operational_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return redacted wedding activation state and isolated event counts."""

    workspace_id = await session.scalar(
        select(Workspace.id).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    binding = None
    event_count = 0
    fact_count = 0
    reply_sent_count = 0
    reply_unknown_count = 0
    material_job_counts: list[dict[str, object]] = []
    material_delivery_counts: list[dict[str, object]] = []
    material_delivery_unknown_count = 0
    checkpoint_lag = 0
    queue_depth = 0
    oldest_nonterminal_age_seconds: int | None = None
    attempts = 0
    stale_recovery_count = 0
    terminal_error_categories: dict[str, int] = {}
    status_now = now or datetime.now(UTC)
    if workspace_id is not None:
        binding = await session.scalar(
            select(BetaSpaceBindingModel).where(
                BetaSpaceBindingModel.workspace_id == workspace_id,
                BetaSpaceBindingModel.space_ref == BETA_WEDDING_SPACE_REF,
            )
        )
        if binding is not None:
            event_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceEventModel.id)).where(
                        BetaSpaceEventModel.workspace_id == workspace_id,
                        BetaSpaceEventModel.binding_id == binding.id,
                    )
                )
                or 0
            )
            fact_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceEventModel.id)).where(
                        BetaSpaceEventModel.workspace_id == workspace_id,
                        BetaSpaceEventModel.binding_id == binding.id,
                        BetaSpaceEventModel.event_kind == "fact",
                    )
                )
                or 0
            )
            reply_sent_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceReplyModel.id)).where(
                        BetaSpaceReplyModel.workspace_id == workspace_id,
                        BetaSpaceReplyModel.binding_id == binding.id,
                        BetaSpaceReplyModel.state == "sent",
                    )
                )
                or 0
            )
            reply_unknown_count = int(
                await session.scalar(
                    select(func.count(BetaSpaceReplyModel.id)).where(
                        BetaSpaceReplyModel.workspace_id == workspace_id,
                        BetaSpaceReplyModel.binding_id == binding.id,
                        BetaSpaceReplyModel.state == "unknown",
                    )
                )
                or 0
            )
            material_job_rows = (
                await session.execute(
                    select(
                        BetaMaterialJobModel.media_kind,
                        BetaMaterialJobModel.state,
                        func.count(BetaMaterialJobModel.id),
                    )
                    .where(
                        BetaMaterialJobModel.workspace_id == workspace_id,
                        BetaMaterialJobModel.binding_id == binding.id,
                    )
                    .group_by(
                        BetaMaterialJobModel.media_kind,
                        BetaMaterialJobModel.state,
                    )
                    .order_by(
                        BetaMaterialJobModel.media_kind,
                        BetaMaterialJobModel.state,
                    )
                )
            ).all()
            material_job_counts = [
                {
                    "media_kind": media_kind,
                    "state": material_state,
                    "count": int(count),
                }
                for media_kind, material_state, count in material_job_rows
            ]
            material_delivery_rows = (
                await session.execute(
                    select(
                        BetaMaterialDeliveryModel.phase,
                        BetaMaterialDeliveryModel.state,
                        func.count(BetaMaterialDeliveryModel.id),
                    )
                    .join(
                        BetaMaterialJobModel,
                        BetaMaterialDeliveryModel.job_id == BetaMaterialJobModel.id,
                    )
                    .where(
                        BetaMaterialJobModel.workspace_id == workspace_id,
                        BetaMaterialJobModel.binding_id == binding.id,
                    )
                    .group_by(
                        BetaMaterialDeliveryModel.phase,
                        BetaMaterialDeliveryModel.state,
                    )
                    .order_by(
                        BetaMaterialDeliveryModel.phase,
                        BetaMaterialDeliveryModel.state,
                    )
                )
            ).all()
            material_delivery_counts = [
                {
                    "phase": phase,
                    "state": delivery_state,
                    "count": int(count),
                }
                for phase, delivery_state, count in material_delivery_rows
            ]
            material_delivery_unknown_count = sum(
                int(count)
                for _phase, delivery_state, count in material_delivery_rows
                if delivery_state == "unknown"
            )

            max_checkpoint_sequence = int(
                await session.scalar(
                    select(func.max(BetaSpaceEventModel.checkpoint_sequence)).where(
                        BetaSpaceEventModel.workspace_id == workspace_id,
                        BetaSpaceEventModel.binding_id == binding.id,
                    )
                )
                or 0
            )
            checkpoint_lag = max(
                0,
                max_checkpoint_sequence
                - int(binding.checkpoint_last_exported_sequence or 0),
            )
            work_queue_depth = int(
                await session.scalar(
                    select(func.count(BetaSpaceWorkItemModel.id)).where(
                        BetaSpaceWorkItemModel.workspace_id == workspace_id,
                        BetaSpaceWorkItemModel.binding_id == binding.id,
                        BetaSpaceWorkItemModel.state.in_(("pending", "leased", "retry")),
                    )
                )
                or 0
            )
            dispatch_queue_depth = int(
                await session.scalar(
                    select(func.count()).where(
                        BetaCheckpointDispatchIntentModel.workspace_id == workspace_id,
                        BetaCheckpointDispatchIntentModel.binding_id == binding.id,
                        BetaCheckpointDispatchIntentModel.state.in_(("pending", "sending")),
                    )
                )
                or 0
            )
            queue_depth = work_queue_depth + dispatch_queue_depth
            oldest_work_at = await session.scalar(
                select(func.min(BetaSpaceWorkItemModel.created_at)).where(
                    BetaSpaceWorkItemModel.workspace_id == workspace_id,
                    BetaSpaceWorkItemModel.binding_id == binding.id,
                    BetaSpaceWorkItemModel.state.in_(("pending", "leased", "retry")),
                )
            )
            oldest_dispatch_at = await session.scalar(
                select(func.min(BetaCheckpointDispatchIntentModel.created_at)).where(
                    BetaCheckpointDispatchIntentModel.workspace_id == workspace_id,
                    BetaCheckpointDispatchIntentModel.binding_id == binding.id,
                    BetaCheckpointDispatchIntentModel.state.in_(("pending", "sending")),
                )
            )
            oldest_nonterminal_at = min(
                (
                    timestamp
                    for timestamp in (oldest_work_at, oldest_dispatch_at)
                    if timestamp is not None
                ),
                default=None,
            )
            if oldest_nonterminal_at is not None:
                oldest_nonterminal_age_seconds = max(
                    0,
                    int((status_now - oldest_nonterminal_at).total_seconds()),
                )
            work_attempts = int(
                await session.scalar(
                    select(func.coalesce(func.sum(BetaSpaceWorkItemModel.attempt_count), 0)).where(
                        BetaSpaceWorkItemModel.workspace_id == workspace_id,
                        BetaSpaceWorkItemModel.binding_id == binding.id,
                    )
                )
                or 0
            )
            dispatch_attempts = int(
                await session.scalar(
                    select(
                        func.coalesce(
                            func.sum(BetaCheckpointDispatchIntentModel.attempt_count),
                            0,
                        )
                    ).where(
                        BetaCheckpointDispatchIntentModel.workspace_id == workspace_id,
                        BetaCheckpointDispatchIntentModel.binding_id == binding.id,
                    )
                )
                or 0
            )
            attempts = work_attempts + dispatch_attempts
            work_stale_recoveries = int(
                await session.scalar(
                    select(func.count(BetaSpaceWorkItemModel.id)).where(
                        BetaSpaceWorkItemModel.workspace_id == workspace_id,
                        BetaSpaceWorkItemModel.binding_id == binding.id,
                        BetaSpaceWorkItemModel.attempt_count > 1,
                    )
                )
                or 0
            )
            dispatch_stale_recoveries = int(
                await session.scalar(
                    select(func.count()).where(
                        BetaCheckpointDispatchIntentModel.workspace_id == workspace_id,
                        BetaCheckpointDispatchIntentModel.binding_id == binding.id,
                        BetaCheckpointDispatchIntentModel.attempt_count > 1,
                    )
                )
                or 0
            )
            stale_recovery_count = work_stale_recoveries + dispatch_stale_recoveries
            terminal_rows = (
                await session.execute(
                    select(
                        BetaSpaceWorkItemModel.last_error_code,
                        func.count(BetaSpaceWorkItemModel.id),
                    )
                    .where(
                        BetaSpaceWorkItemModel.workspace_id == workspace_id,
                        BetaSpaceWorkItemModel.binding_id == binding.id,
                        BetaSpaceWorkItemModel.state.in_(("failed", "unknown")),
                        BetaSpaceWorkItemModel.last_error_code.is_not(None),
                    )
                    .group_by(BetaSpaceWorkItemModel.last_error_code)
                )
            ).all()
            dispatch_terminal_rows = (
                await session.execute(
                    select(
                        BetaCheckpointDispatchIntentModel.last_error_code,
                        func.count(),
                    )
                    .where(
                        BetaCheckpointDispatchIntentModel.workspace_id == workspace_id,
                        BetaCheckpointDispatchIntentModel.binding_id == binding.id,
                        BetaCheckpointDispatchIntentModel.state.in_(("failed", "unknown")),
                        BetaCheckpointDispatchIntentModel.last_error_code.is_not(None),
                    )
                    .group_by(BetaCheckpointDispatchIntentModel.last_error_code)
                )
            ).all()
            for error_code, count in (*terminal_rows, *dispatch_terminal_rows):
                category = _terminal_error_category(str(error_code))
                terminal_error_categories[category] = (
                    terminal_error_categories.get(category, 0) + int(count)
                )
            terminal_error_categories = dict(sorted(terminal_error_categories.items()))

    binding_state = binding.state if binding is not None else "absent"
    provenance_state = (
        binding.provenance_state if binding is not None else "absent"
    )
    actor_policy = binding.actor_policy if binding is not None else "absent"
    binding_live = wedding_binding_is_verified(binding)
    if not beta_space_production_ready(settings):
        state = "connector_not_ready"
    elif binding is None:
        state = "awaiting_binding"
    elif binding_live:
        state = "active"
    elif provenance_state == "quarantined" or binding_state == "quarantined":
        state = "quarantined"
    else:
        state = "revoked"

    return {
        "state": state,
        "gates": {
            "production": settings.gate_env == "production",
            "connector": settings.gate_beta_connector_enabled,
            "privacy_mode_confirmed": settings.gate_beta_privacy_mode_confirmed,
            "space_enabled": binding_live,
            "space_ready": binding_live,
            "delivery_enabled": binding_live,
        },
        "configured": {
            "chat_binding": binding is not None,
            "subject_binding": binding is not None,
            "activation_digest": binding is not None,
            "actor_policy_explicit": binding is not None,
            "provenance_recorded": binding is not None,
        },
        "binding_state": binding_state,
        "actor_policy": actor_policy,
        "provenance_state": provenance_state,
        "provenance_evidence": (
            binding.provenance_evidence_kind if binding is not None else "absent"
        ),
        "production_ready": beta_space_production_ready(settings) and binding_live,
        "event_count": event_count,
        "fact_count": fact_count,
        "reply_sent_count": reply_sent_count,
        "reply_unknown_count": reply_unknown_count,
        "material_job_counts": material_job_counts,
        "material_delivery_counts": material_delivery_counts,
        "material_delivery_unknown_count": material_delivery_unknown_count,
        "queue_depth": queue_depth,
        "checkpoint_lag": checkpoint_lag,
        "oldest_nonterminal_age_seconds": oldest_nonterminal_age_seconds,
        "attempts": attempts,
        "stale_recovery_count": stale_recovery_count,
        "terminal_error_categories": terminal_error_categories,
        "private_identifiers_exposed": False,
        "private_payload_exposed": False,
    }
