"""Validation and aggregate status for BECOME I4 recovery receipts."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.become_i4_chain import I4_CONTRACT_DIGEST, JsonObject, canonical_digest
from app.infrastructure.become_i4_chain import (
    MAYAKI_SOURCE_SHA,
    OKO_SOURCE_SHA,
    TAKE_SOURCE_SHA,
)
from app.models.become import (
    BecomeVoiceChainSlotModel,
    BecomeVoiceCorrelationModel,
)
from app.models.become_recovery import (
    BecomeI4RecoveryAttemptModel,
    BecomeI4RecoveryReceiptModel,
    BecomeI4RecoveryStageEventModel,
)
from app.models.canonical import Workspace

RECOVERY_SCHEMA = "gate.become-i4-recovery-receipt@1"
RECOVERY_ATTEMPT_SCHEMA = "gate.become-i4-recovery-attempt@2"
RECOVERY_TARGET_SOURCE_SHA = MAYAKI_SOURCE_SHA
_RECOVERY_FIELDS = frozenset(
    {
        "schema",
        "stage",
        "attempt_version",
        "correlation_ref",
        "original_request_digest",
        "original_receipt_digest",
        "original_source_sha",
        "target_source_sha",
        "state",
        "enabled",
        "external_effects",
    }
)
_ATTEMPT_FIELDS = frozenset(
    {
        "schema",
        "attempt_ref",
        "attempt_version",
        "correlation_ref",
        "prepared_recovery_digest",
        "original_failed_request_digest",
        "original_failed_receipt_digest",
        "relations_receipt_digest",
        "transcript_digest",
        "contract_digest",
        "source_shas",
        "stage_order",
        "max_posts",
        "state",
        "external_effects",
    }
)


def build_recovery_payload(
    *,
    correlation_ref: str,
    original_request_digest: str,
    original_receipt_digest: str,
    original_source_sha: str,
) -> JsonObject:
    """Build a new attempt version without changing the failed source slot."""

    payload: JsonObject = {
        "schema": RECOVERY_SCHEMA,
        "stage": "mayaki",
        "attempt_version": 2,
        "correlation_ref": correlation_ref,
        "original_request_digest": original_request_digest,
        "original_receipt_digest": original_receipt_digest,
        "original_source_sha": original_source_sha,
        "target_source_sha": RECOVERY_TARGET_SOURCE_SHA,
        "state": "prepared",
        "enabled": False,
        "external_effects": 0,
    }
    return validate_recovery_payload(payload)


def validate_recovery_payload(raw: object) -> JsonObject:
    """Fail closed on any mutated recovery replay."""

    if not isinstance(raw, dict) or frozenset(raw) != _RECOVERY_FIELDS:
        raise ValueError("become_i4_recovery_fields_invalid")
    if (
        raw.get("schema") != RECOVERY_SCHEMA
        or raw.get("stage") != "mayaki"
        or raw.get("attempt_version") != 2
        or raw.get("state") != "prepared"
        or raw.get("enabled") is not False
        or raw.get("external_effects") != 0
        or raw.get("target_source_sha") != RECOVERY_TARGET_SOURCE_SHA
    ):
        raise ValueError("become_i4_recovery_binding_invalid")
    correlation_ref = raw.get("correlation_ref")
    if not isinstance(correlation_ref, str) or not correlation_ref.startswith(
        "synthetic://correlation/voice/"
    ):
        raise ValueError("become_i4_recovery_correlation_invalid")
    for field, size in (
        ("original_request_digest", 64),
        ("original_receipt_digest", 64),
        ("original_source_sha", 40),
    ):
        value = raw.get(field)
        if not isinstance(value, str) or len(value) != size:
            raise ValueError("become_i4_recovery_digest_invalid")
    return cast(JsonObject, raw)


def validate_recovery_replay(
    raw: object,
    *,
    expected_digest: str,
) -> JsonObject:
    """Reject any structurally valid payload whose canonical digest changed."""

    payload = validate_recovery_payload(raw)
    if canonical_digest(payload) != expected_digest:
        raise ValueError("become_i4_recovery_mutated_replay")
    return payload


def validate_recovery_attempt_replay(
    raw: object,
    *,
    expected_digest: str,
) -> JsonObject:
    """Validate the immutable attempt identity and all frozen execution pins."""

    if not isinstance(raw, dict) or frozenset(raw) != _ATTEMPT_FIELDS:
        raise ValueError("become_i4_recovery_attempt_fields_invalid")
    if (
        raw.get("schema") != RECOVERY_ATTEMPT_SCHEMA
        or raw.get("attempt_version") != 2
        or raw.get("stage_order") != ["mayaki", "oko", "take"]
        or raw.get("max_posts") != 3
        or raw.get("state") != "prepared"
        or raw.get("external_effects") != 0
        or raw.get("contract_digest") != I4_CONTRACT_DIGEST
        or raw.get("source_shas")
        != {
            "mayaki": MAYAKI_SOURCE_SHA,
            "oko": OKO_SOURCE_SHA,
            "take": TAKE_SOURCE_SHA,
        }
    ):
        raise ValueError("become_i4_recovery_attempt_binding_invalid")
    for field in (
        "prepared_recovery_digest",
        "original_failed_request_digest",
        "original_failed_receipt_digest",
        "relations_receipt_digest",
        "transcript_digest",
    ):
        value = raw.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("become_i4_recovery_attempt_digest_invalid")
    for field in ("attempt_ref", "correlation_ref"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.startswith("synthetic://"):
            raise ValueError("become_i4_recovery_attempt_ref_invalid")
    if canonical_digest(raw) != expected_digest:
        raise ValueError("become_i4_recovery_attempt_mutated_replay")
    return cast(JsonObject, raw)


async def become_i4_recovery_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    execution_enabled: bool = False,
    max_posts: int = 0,
) -> dict[str, object]:
    """Expose content-free immutable preparation plus append-only v2 outcomes."""

    rows = (
        await session.execute(
            select(
                BecomeI4RecoveryReceiptModel,
                BecomeVoiceChainSlotModel,
                BecomeVoiceCorrelationModel,
            )
            .join(
                Workspace,
                Workspace.id == BecomeI4RecoveryReceiptModel.workspace_id,
            )
            .join(
                BecomeVoiceChainSlotModel,
                BecomeVoiceChainSlotModel.id
                == BecomeI4RecoveryReceiptModel.failed_slot_id,
            )
            .join(
                BecomeVoiceCorrelationModel,
                BecomeVoiceCorrelationModel.id
                == BecomeI4RecoveryReceiptModel.correlation_id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
            )
        )
    ).all()
    prepared = 0
    source_failure_preserved = True
    target_source_sha: str | None = None
    attempt_version: int | None = None
    for recovery, slot, correlation in rows:
        payload = validate_recovery_replay(
            recovery.receipt_payload,
            expected_digest=recovery.receipt_digest,
        )
        if (
            recovery.schema_name != RECOVERY_SCHEMA
            or recovery.stage != "mayaki"
            or recovery.state != "prepared"
            or recovery.enabled is not False
            or recovery.attempt_version != 2
            or recovery.original_request_digest != slot.request_digest
            or recovery.original_receipt_digest != slot.receipt_digest
            or recovery.original_source_sha != slot.source_sha
            or recovery.target_source_sha != RECOVERY_TARGET_SOURCE_SHA
            or payload["correlation_ref"] != correlation.correlation_ref
        ):
            raise ValueError("become_i4_recovery_record_invalid")
        failure = slot.receipt_payload
        if (
            slot.stage != "mayaki"
            or slot.state != "failed"
            or not isinstance(failure, dict)
            or failure.get("schema") != "gate.become-i4-stage-outcome@1"
            or failure.get("classification") != "failed"
            or failure.get("error_code") != "connection_not_established"
            or failure.get("external_effects") != 0
        ):
            source_failure_preserved = False
        prepared += 1
        target_source_sha = recovery.target_source_sha
        attempt_version = recovery.attempt_version

    attempts = (
        await session.scalars(
            select(BecomeI4RecoveryAttemptModel)
            .join(
                Workspace,
                Workspace.id == BecomeI4RecoveryAttemptModel.workspace_id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
            )
        )
    ).all()
    if len(attempts) > 1:
        raise ValueError("become_i4_recovery_attempt_cardinality_invalid")

    events: list[BecomeI4RecoveryStageEventModel] = []
    attempt_digest: str | None = None
    prepared_digest: str | None = None
    if attempts:
        attempt = attempts[0]
        payload = validate_recovery_attempt_replay(
            attempt.attempt_payload,
            expected_digest=attempt.attempt_digest,
        )
        if (
            attempt.schema_name != RECOVERY_ATTEMPT_SCHEMA
            or attempt.attempt_version != 2
            or attempt.state != "prepared"
            or attempt.max_posts != 3
            or attempt.contract_digest != I4_CONTRACT_DIGEST
            or attempt.mayaki_source_sha != MAYAKI_SOURCE_SHA
            or attempt.oko_source_sha != OKO_SOURCE_SHA
            or attempt.take_source_sha != TAKE_SOURCE_SHA
            or payload["prepared_recovery_digest"]
            != attempt.prepared_recovery_digest
        ):
            raise ValueError("become_i4_recovery_attempt_record_invalid")
        attempt_digest = attempt.attempt_digest
        prepared_digest = attempt.prepared_recovery_digest
        events = list(
            (
                await session.scalars(
                    select(BecomeI4RecoveryStageEventModel)
                    .where(
                        BecomeI4RecoveryStageEventModel.attempt_id
                        == attempt.id,
                    )
                    .order_by(
                        BecomeI4RecoveryStageEventModel.ordinal,
                        BecomeI4RecoveryStageEventModel.event_sequence,
                    )
                )
            ).all()
        )
        _validate_status_events(attempt=attempt, events=events)

    stage_states = {
        stage: _stage_state(events, stage=stage)
        for stage in ("mayaki", "oko", "take")
    }
    reserved_counts = {
        stage: sum(
            item.stage == stage and item.event_type == "reserved"
            for item in events
        )
        for stage in ("mayaki", "oko", "take")
    }
    terminal = _terminal_state(stage_states)
    unknown = any(value == "unknown" for value in stage_states.values())
    take_terminal = next(
        (
            item
            for item in events
            if item.stage == "take"
            and item.event_sequence == 2
            and item.event_type == "completed"
        ),
        None,
    )
    result: dict[str, object] = {
        "chain_recovery_receipt_count": len(rows),
        "chain_recovery_prepared_count": prepared,
        "chain_recovery_prepared_receipt_enabled": False,
        "chain_recovery_enabled": execution_enabled,
        "chain_recovery_activation_max_posts": max_posts,
        "chain_recovery_attempt_version": attempt_version,
        "chain_recovery_target_source_sha": target_source_sha,
        "chain_recovery_source_failure_preserved": (
            len(rows) == 1 and prepared == 1 and source_failure_preserved
        ),
        "chain_recovery_attempt_count": len(attempts),
        "chain_recovery_activation_receipt_count": len(attempts),
        "chain_recovery_attempt_digest": attempt_digest,
        "chain_recovery_prepared_digest": prepared_digest,
        "chain_recovery_attempt_state": terminal,
        "chain_recovery_post_count": sum(reserved_counts.values()),
        "chain_recovery_reconciliation_required": unknown,
        "chain_recovery_mayaki_source_sha": MAYAKI_SOURCE_SHA,
        "chain_recovery_oko_source_sha": OKO_SOURCE_SHA,
        "chain_recovery_take_source_sha": TAKE_SOURCE_SHA,
        "chain_recovery_take_receipt_digest": (
            take_terminal.receipt_digest if take_terminal is not None else None
        ),
    }
    for stage in ("mayaki", "oko", "take"):
        terminal_event = next(
            (
                item
                for item in events
                if item.stage == stage and item.event_sequence == 2
            ),
            None,
        )
        reservation = next(
            (
                item
                for item in events
                if item.stage == stage and item.event_sequence == 1
            ),
            None,
        )
        result[f"chain_recovery_{stage}_state"] = stage_states[stage]
        result[f"chain_recovery_{stage}_post_count"] = reserved_counts[stage]
        result[f"chain_recovery_{stage}_request_digest"] = (
            reservation.request_digest if reservation is not None else None
        )
        result[f"chain_recovery_{stage}_receipt_digest"] = (
            terminal_event.receipt_digest
            if terminal_event is not None
            else None
        )
    return result


def _validate_status_events(
    *,
    attempt: BecomeI4RecoveryAttemptModel,
    events: list[BecomeI4RecoveryStageEventModel],
) -> None:
    seen: set[tuple[str, int]] = set()
    for event in events:
        identity = (event.stage, event.event_sequence)
        if identity in seen:
            raise ValueError("become_i4_recovery_duplicate_event")
        seen.add(identity)
        payload = event.event_payload
        if (
            not isinstance(payload, dict)
            or event.attempt_id != attempt.id
            or event.workspace_id != attempt.workspace_id
            or event.event_digest != canonical_digest(payload)
            or payload.get("attempt_digest") != attempt.attempt_digest
            or payload.get("stage") != event.stage
            or payload.get("event_sequence") != event.event_sequence
            or payload.get("event_type") != event.event_type
            or payload.get("request_digest") != event.request_digest
            or payload.get("source_sha") != event.source_sha
            or payload.get("receipt_digest") != event.receipt_digest
            or canonical_digest(event.request_payload) != event.request_digest
            or (
                event.receipt_payload is not None
                and canonical_digest(event.receipt_payload)
                != event.receipt_digest
            )
        ):
            raise ValueError("become_i4_recovery_event_record_invalid")


def _stage_state(
    events: list[BecomeI4RecoveryStageEventModel],
    *,
    stage: str,
) -> str:
    terminal = next(
        (
            item
            for item in events
            if item.stage == stage and item.event_sequence == 2
        ),
        None,
    )
    if terminal is not None:
        return terminal.event_type
    if any(
        item.stage == stage and item.event_sequence == 1
        for item in events
    ):
        return "reserved"
    return "not_started"


def _terminal_state(states: dict[str, str]) -> str:
    for stage in ("mayaki", "oko", "take"):
        state = states[stage]
        if state in {"failed", "unknown"}:
            return state
        if state != "completed":
            return "prepared" if stage == "mayaki" and state == "not_started" else "in_progress"
    return "completed"
