"""Durable two-phase encrypted checkpoint batches for Beta Git sync."""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.beta_checkpoint_export import (
    MAX_CHECKPOINT_EVENTS,
    BetaCheckpointExportError,
    _build_portable_checkpoint,
    _byte_bounded_event_prefix,
    _iso_datetime,
    _portable_event,
)
from app.models.beta_context import (
    BetaContextBindingModel,
    BetaContextCheckpointExportModel,
    BetaContextCheckpointStateModel,
    BetaContextEventModel,
)

MAX_BATCH_EVENTS = MAX_CHECKPOINT_EVENTS
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BetaCheckpointBatchError(RuntimeError):
    """Fail-closed batch error carrying only a stable public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def next_beta_checkpoint_batch(
    session: AsyncSession,
    *,
    installation_ref: str,
    expected_user_workspace_id: str,
    expected_group_id: str,
) -> dict[str, Any] | None:
    """Create or replay one pending encrypted batch without cursor advance."""

    binding = await session.scalar(
        select(BetaContextBindingModel)
        .where(
            BetaContextBindingModel.installation_ref == installation_ref,
            BetaContextBindingModel.state == "active",
        )
        .with_for_update()
    )
    _require_exact_binding(
        binding,
        expected_user_workspace_id=expected_user_workspace_id,
        expected_group_id=expected_group_id,
    )
    assert binding is not None
    recipient_public_key = binding.checkpoint_recipient_pem
    assert recipient_public_key is not None

    state = await session.scalar(
        select(BetaContextCheckpointStateModel)
        .where(
            BetaContextCheckpointStateModel.installation_id
            == binding.installation_id
        )
        .with_for_update()
    )
    if state is None or state.group_id != binding.group_id:
        raise BetaCheckpointBatchError("beta_checkpoint_state_not_exact")

    pending_rows = (
        await session.execute(
            select(BetaContextCheckpointExportModel)
            .where(
                BetaContextCheckpointExportModel.installation_id
                == binding.installation_id,
                BetaContextCheckpointExportModel.state == "pending",
            )
            .order_by(BetaContextCheckpointExportModel.sequence_start)
        )
    ).scalars().all()
    if len(pending_rows) > 1:
        raise BetaCheckpointBatchError("beta_checkpoint_pending_ambiguous")
    if pending_rows:
        pending = pending_rows[0]
        if pending.sequence_start != state.last_exported_sequence + 1:
            raise BetaCheckpointBatchError("beta_checkpoint_pending_gap")
        return _batch_payload(binding, state.last_sequence, pending, replay=True)

    sequence_start = state.last_exported_sequence + 1
    rows = (
        await session.execute(
            select(BetaContextEventModel)
            .where(
                BetaContextEventModel.installation_id
                == binding.installation_id,
                BetaContextEventModel.group_id == binding.group_id,
                BetaContextEventModel.sequence >= sequence_start,
            )
            .order_by(BetaContextEventModel.sequence)
            .limit(MAX_BATCH_EVENTS)
        )
    ).scalars().all()
    if not rows:
        return None
    if rows[0].sequence != sequence_start:
        raise BetaCheckpointBatchError("beta_checkpoint_event_gap")
    for offset, row in enumerate(rows):
        if row.sequence != sequence_start + offset:
            raise BetaCheckpointBatchError("beta_checkpoint_event_gap")

    events = [_portable_event(row) for row in rows]
    try:
        events = _byte_bounded_event_prefix(
            group_id=binding.group_id,
            events=events,
        )
    except BetaCheckpointExportError as exc:
        raise BetaCheckpointBatchError(exc.code) from exc
    try:
        checkpoint = _build_portable_checkpoint(
            group_id=binding.group_id,
            events=events,
            recipient_public_key=recipient_public_key.encode("utf-8"),
            created_at=_iso_datetime(datetime.now(UTC)),
        )
    except BetaCheckpointExportError as exc:
        raise BetaCheckpointBatchError(exc.code) from exc

    manifest = checkpoint["manifest"]
    pending = BetaContextCheckpointExportModel(
        installation_id=binding.installation_id,
        group_id=binding.group_id,
        sequence_start=manifest["sequence_start"],
        sequence_end=manifest["sequence_end"],
        event_count=manifest["event_count"],
        container=checkpoint["container"],
        ciphertext_sha256=manifest["ciphertext_sha256"],
        manifest=manifest,
        state="pending",
    )
    session.add(pending)
    await session.flush()
    return _batch_payload(binding, state.last_sequence, pending, replay=False)


async def acknowledge_beta_checkpoint_batch(
    session: AsyncSession,
    *,
    installation_ref: str,
    expected_user_workspace_id: str,
    expected_group_id: str,
    sequence_start: int,
    sequence_end: int,
    ciphertext_sha256: str,
    github_commit_sha: str,
) -> dict[str, Any]:
    """Advance the export cursor only for the exact committed pending batch."""

    if (
        sequence_start < 1
        or sequence_end < sequence_start
        or _SHA256.fullmatch(ciphertext_sha256) is None
        or _GIT_SHA.fullmatch(github_commit_sha) is None
    ):
        raise BetaCheckpointBatchError("beta_checkpoint_ack_invalid")

    binding = await session.scalar(
        select(BetaContextBindingModel)
        .where(
            BetaContextBindingModel.installation_ref == installation_ref,
            BetaContextBindingModel.state == "active",
        )
        .with_for_update()
    )
    _require_exact_binding(
        binding,
        expected_user_workspace_id=expected_user_workspace_id,
        expected_group_id=expected_group_id,
    )
    assert binding is not None

    state = await session.scalar(
        select(BetaContextCheckpointStateModel)
        .where(
            BetaContextCheckpointStateModel.installation_id
            == binding.installation_id
        )
        .with_for_update()
    )
    if state is None or state.group_id != binding.group_id:
        raise BetaCheckpointBatchError("beta_checkpoint_state_not_exact")

    batch = await session.scalar(
        select(BetaContextCheckpointExportModel)
        .where(
            BetaContextCheckpointExportModel.installation_id
            == binding.installation_id,
            BetaContextCheckpointExportModel.sequence_start == sequence_start,
        )
        .with_for_update()
    )
    if batch is None:
        raise BetaCheckpointBatchError("beta_checkpoint_pending_missing")
    if (
        batch.group_id != binding.group_id
        or batch.sequence_end != sequence_end
        or batch.ciphertext_sha256 != ciphertext_sha256
    ):
        raise BetaCheckpointBatchError("beta_checkpoint_ack_mismatch")

    if batch.state == "acknowledged":
        if batch.github_commit_sha != github_commit_sha:
            raise BetaCheckpointBatchError("beta_checkpoint_ack_commit_conflict")
        return _ack_payload(state, batch, replay=True)

    if (
        batch.state != "pending"
        or state.last_exported_sequence != sequence_start - 1
        or sequence_end > state.last_sequence
    ):
        raise BetaCheckpointBatchError("beta_checkpoint_ack_not_contiguous")

    # The compatibility model has no canonical portable Space path.  Advancing
    # it from SHA syntax alone would retain B-011, so new ACKs fail closed until
    # that legacy binding is migrated to an exact Beta Space binding.
    raise BetaCheckpointBatchError("beta_checkpoint_artifact_proof_required")



def _require_exact_binding(
    binding: BetaContextBindingModel | None,
    *,
    expected_user_workspace_id: str,
    expected_group_id: str,
) -> None:
    if binding is None:
        raise BetaCheckpointBatchError("beta_checkpoint_binding_not_active")
    if (
        binding.user_workspace_id != expected_user_workspace_id
        or binding.group_id != expected_group_id
    ):
        raise BetaCheckpointBatchError("beta_checkpoint_binding_not_exact")
    if not binding.checkpoint_recipient_pem:
        raise BetaCheckpointBatchError("beta_checkpoint_recipient_missing")


def _batch_payload(
    binding: BetaContextBindingModel,
    last_sequence: int,
    batch: BetaContextCheckpointExportModel,
    *,
    replay: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "beta.checkpoint-batch@1",
        "installation_ref": binding.installation_ref,
        "user_workspace_id": binding.user_workspace_id,
        "group_id": binding.group_id,
        "sequence_start": batch.sequence_start,
        "sequence_end": batch.sequence_end,
        "event_count": batch.event_count,
        "container_encoding": "base64",
        "container_base64": base64.b64encode(batch.container).decode("ascii"),
        "manifest": batch.manifest,
        "replay": replay,
        "accepted_sequence": last_sequence,
        "checkpoint_lag_events": max(0, last_sequence - batch.sequence_end),
        "plaintext_context_exposed": False,
        "telegram_private_data_exposed": False,
    }


def _ack_payload(
    state: BetaContextCheckpointStateModel,
    batch: BetaContextCheckpointExportModel,
    *,
    replay: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "beta.checkpoint-ack@1",
        "group_id": batch.group_id,
        "sequence_start": batch.sequence_start,
        "sequence_end": batch.sequence_end,
        "ciphertext_sha256": batch.ciphertext_sha256,
        "github_commit_sha": batch.github_commit_sha,
        "last_exported_sequence": state.last_exported_sequence,
        "checkpoint_lag_events": max(
            0, state.last_sequence - state.last_exported_sequence
        ),
        "replay": replay,
        "plaintext_context_exposed": False,
        "telegram_private_data_exposed": False,
    }
