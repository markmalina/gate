"""Validation and aggregate status for default-off BECOME I4 recovery receipts."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.become_i4_chain import JsonObject, canonical_digest
from app.models.become import (
    BecomeVoiceChainSlotModel,
    BecomeVoiceCorrelationModel,
)
from app.models.become_recovery import BecomeI4RecoveryReceiptModel
from app.models.canonical import Workspace

RECOVERY_SCHEMA = "gate.become-i4-recovery-receipt@1"
RECOVERY_TARGET_SOURCE_SHA = "2078d230852af1c7e3a309f00d716fa2fc664f68"
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


async def become_i4_recovery_status(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, object]:
    """Expose only aggregate recovery evidence and exact target source."""

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
    enabled = False
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
        enabled = enabled or recovery.enabled
        target_source_sha = recovery.target_source_sha
        attempt_version = recovery.attempt_version
    return {
        "chain_recovery_receipt_count": len(rows),
        "chain_recovery_prepared_count": prepared,
        "chain_recovery_enabled": enabled,
        "chain_recovery_attempt_version": attempt_version,
        "chain_recovery_target_source_sha": target_source_sha,
        "chain_recovery_source_failure_preserved": (
            len(rows) == 1 and prepared == 1 and source_failure_preserved
        ),
    }
