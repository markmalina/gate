"""Encrypted two-phase checkpoints for the live Wedding Beta Space."""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.beta_checkpoint_batches import BetaCheckpointBatchError
from app.infrastructure.beta_checkpoint_export import (
    BetaCheckpointExportError,
    _build_portable_checkpoint,
    _iso_datetime,
)
from app.models.beta_material import BetaMaterialJobModel
from app.models.beta_space import (
    BetaSpaceBindingModel,
    BetaSpaceCheckpointExportModel,
    BetaSpaceEventModel,
)
from app.models.beta_wedding_voice import BetaWeddingVoiceJobModel
from app.models.domain import TelegramReceipt

MAX_BATCH_EVENTS = 50
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BetaSpaceCheckpointBatchError(BetaCheckpointBatchError):
    """Fail-closed Wedding checkpoint error carrying only a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def next_beta_space_checkpoint_batch(
    session: AsyncSession,
    *,
    installation_ref: str,
    expected_user_workspace_id: str,
    expected_group_id: str,
) -> dict[str, Any] | None:
    """Create or replay one Wedding checkpoint without cursor advance."""

    binding = await _exact_binding(
        session,
        installation_ref=installation_ref,
        expected_user_workspace_id=expected_user_workspace_id,
        expected_group_id=expected_group_id,
    )
    pending_rows = (
        await session.scalars(
            select(BetaSpaceCheckpointExportModel)
            .where(
                BetaSpaceCheckpointExportModel.binding_id == binding.id,
                BetaSpaceCheckpointExportModel.state == "pending",
            )
            .order_by(BetaSpaceCheckpointExportModel.sequence_start)
        )
    ).all()
    if len(pending_rows) > 1:
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_pending_ambiguous")

    accepted_sequence = int(
        await session.scalar(
            select(func.coalesce(func.max(BetaSpaceEventModel.checkpoint_sequence), 0))
            .where(BetaSpaceEventModel.binding_id == binding.id)
        )
        or 0
    )
    if pending_rows:
        pending = pending_rows[0]
        if pending.sequence_start != binding.checkpoint_last_exported_sequence + 1:
            raise BetaSpaceCheckpointBatchError("beta_checkpoint_pending_gap")
        return _batch_payload(binding, accepted_sequence, pending, replay=True)

    sequence_start = binding.checkpoint_last_exported_sequence + 1
    rows = (
        await session.execute(
            select(
                BetaSpaceEventModel,
                TelegramReceipt,
                BetaWeddingVoiceJobModel,
                BetaMaterialJobModel,
            )
            .join(
                TelegramReceipt,
                TelegramReceipt.id == BetaSpaceEventModel.receipt_id,
            )
            .outerjoin(
                BetaWeddingVoiceJobModel,
                BetaWeddingVoiceJobModel.event_id == BetaSpaceEventModel.id,
            )
            .outerjoin(
                BetaMaterialJobModel,
                BetaMaterialJobModel.event_id == BetaSpaceEventModel.id,
            )
            .where(
                BetaSpaceEventModel.binding_id == binding.id,
                BetaSpaceEventModel.checkpoint_sequence >= sequence_start,
            )
            .order_by(BetaSpaceEventModel.checkpoint_sequence)
            .limit(MAX_BATCH_EVENTS)
        )
    ).all()
    if not rows:
        return None
    if rows[0][0].checkpoint_sequence != sequence_start:
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_event_gap")
    for offset, (event, _receipt, _voice, _material) in enumerate(rows):
        if event.checkpoint_sequence != sequence_start + offset:
            raise BetaSpaceCheckpointBatchError("beta_checkpoint_event_gap")

    events = [
        _portable_wedding_event(
            binding=binding,
            event=event,
            receipt=receipt,
            voice=voice,
            material=material,
        )
        for event, receipt, voice, material in rows
    ]
    recipient_public_key = binding.checkpoint_recipient_pem
    assert recipient_public_key is not None
    try:
        checkpoint = _build_portable_checkpoint(
            group_id=binding.storage_ref,
            events=events,
            recipient_public_key=recipient_public_key.encode("utf-8"),
            created_at=_iso_datetime(datetime.now(UTC)),
        )
    except BetaCheckpointExportError as exc:
        raise BetaSpaceCheckpointBatchError(exc.code) from exc

    manifest = checkpoint["manifest"]
    pending = BetaSpaceCheckpointExportModel(
        binding_id=binding.id,
        group_id=binding.storage_ref,
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
    return _batch_payload(binding, accepted_sequence, pending, replay=False)


async def acknowledge_beta_space_checkpoint_batch(
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
    """Advance the Wedding cursor only for one exact durable Git commit."""

    if (
        sequence_start < 1
        or sequence_end < sequence_start
        or _SHA256.fullmatch(ciphertext_sha256) is None
        or _GIT_SHA.fullmatch(github_commit_sha) is None
    ):
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_ack_invalid")

    binding = await _exact_binding(
        session,
        installation_ref=installation_ref,
        expected_user_workspace_id=expected_user_workspace_id,
        expected_group_id=expected_group_id,
    )
    batch = await session.scalar(
        select(BetaSpaceCheckpointExportModel)
        .where(
            BetaSpaceCheckpointExportModel.binding_id == binding.id,
            BetaSpaceCheckpointExportModel.sequence_start == sequence_start,
        )
        .with_for_update()
    )
    if batch is None:
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_pending_missing")
    if (
        batch.group_id != binding.storage_ref
        or batch.sequence_end != sequence_end
        or batch.ciphertext_sha256 != ciphertext_sha256
    ):
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_ack_mismatch")

    if batch.state == "acknowledged":
        if batch.github_commit_sha != github_commit_sha:
            raise BetaSpaceCheckpointBatchError(
                "beta_checkpoint_ack_commit_conflict"
            )
        return _ack_payload(binding, batch, replay=True)

    accepted_sequence = int(
        await session.scalar(
            select(func.coalesce(func.max(BetaSpaceEventModel.checkpoint_sequence), 0))
            .where(BetaSpaceEventModel.binding_id == binding.id)
        )
        or 0
    )
    if (
        batch.state != "pending"
        or binding.checkpoint_last_exported_sequence != sequence_start - 1
        or sequence_end > accepted_sequence
    ):
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_ack_not_contiguous")

    batch.state = "acknowledged"
    batch.github_commit_sha = github_commit_sha
    batch.acknowledged_at = datetime.now(UTC)
    binding.checkpoint_last_exported_sequence = sequence_end
    binding.updated_at = datetime.now(UTC)
    await session.flush()
    return _ack_payload(binding, batch, replay=False)


async def _exact_binding(
    session: AsyncSession,
    *,
    installation_ref: str,
    expected_user_workspace_id: str,
    expected_group_id: str,
) -> BetaSpaceBindingModel:
    binding = await session.scalar(
        select(BetaSpaceBindingModel)
        .where(
            BetaSpaceBindingModel.installation_ref == installation_ref,
            BetaSpaceBindingModel.state == "consented",
        )
        .with_for_update()
    )
    if binding is None:
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_binding_not_active")
    if (
        binding.tenant_ref != expected_user_workspace_id
        or binding.storage_ref != expected_group_id
    ):
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_binding_not_exact")
    if not binding.checkpoint_recipient_pem:
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_recipient_missing")
    return binding


def _portable_wedding_event(
    *,
    binding: BetaSpaceBindingModel,
    event: BetaSpaceEventModel,
    receipt: TelegramReceipt,
    voice: BetaWeddingVoiceJobModel | None,
    material: BetaMaterialJobModel | None = None,
) -> dict[str, Any]:
    text_value = _event_text(
        event=event,
        receipt=receipt,
        voice=voice,
        material=material,
    )
    content = {
        "format": "structured_text",
        "text": text_value,
        "language": "ru",
    }
    content_sha256 = hashlib.sha256(text_value.encode()).hexdigest()
    source_digest = hashlib.sha256(
        f"{binding.installation_ref}:{event.id}".encode()
    ).hexdigest()
    actor_digest = hashlib.sha256(
        f"{binding.installation_ref}:{event.telegram_user_id}".encode()
    ).hexdigest()
    return {
        "schema_version": "beta.context-event@1",
        "event_id": f"beta-event-{uuid.uuid5(uuid.NAMESPACE_URL, source_digest).hex}",
        "group_id": binding.storage_ref,
        "sequence": event.checkpoint_sequence,
        "kind": "message.inbound",
        "occurred_at": _iso_datetime(event.occurred_at),
        "recorded_at": _iso_datetime(event.created_at),
        "actor_ref": f"actor-{actor_digest[:32]}",
        "content": content,
        "provenance": {
            "source": "telegram",
            "source_event_ref": f"gate-event-{source_digest}",
            "content_sha256": content_sha256,
            "idempotency_key": f"gate.context.{source_digest[:32]}",
        },
        "supersedes": None,
        "tombstones": None,
    }


def _event_text(
    *,
    event: BetaSpaceEventModel,
    receipt: TelegramReceipt,
    voice: BetaWeddingVoiceJobModel | None,
    material: BetaMaterialJobModel | None,
) -> str:
    if material is not None:
        if material.extracted_text and material.structured_text:
            return (
                f"Извлечённое содержание ({material.media_kind}):\n"
                f"{material.extracted_text}\n\n"
                f"Структура Беты:\n{material.structured_text}"
            )
        return (
            f"Получен материал типа {material.media_kind}; "
            "надёжное извлечение содержания не завершено."
        )
    if event.media_kind == "voice":
        if voice is not None and voice.transcript_text and voice.structured_text:
            return (
                f"Транскрипт голосового:\n{voice.transcript_text}\n\n"
                f"Структура Беты:\n{voice.structured_text}"
            )
        return "Получено голосовое сообщение; расшифровка недоступна."
    if event.media_kind in {"photo", "document"}:
        return (
            f"Получен материал типа {event.media_kind}; "
            "содержание пока не распознано."
        )
    payload = receipt.payload
    message = payload.get("message") if isinstance(payload, dict) else None
    text_value = message.get("text") if isinstance(message, dict) else None
    if not isinstance(text_value, str) or not text_value:
        raise BetaSpaceCheckpointBatchError("beta_checkpoint_event_invalid")
    return text_value


def _batch_payload(
    binding: BetaSpaceBindingModel,
    accepted_sequence: int,
    batch: BetaSpaceCheckpointExportModel,
    *,
    replay: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "beta.checkpoint-batch@1",
        "installation_ref": binding.installation_ref,
        "user_workspace_id": binding.tenant_ref,
        "group_id": binding.storage_ref,
        "sequence_start": batch.sequence_start,
        "sequence_end": batch.sequence_end,
        "event_count": batch.event_count,
        "container_encoding": "base64",
        "container_base64": base64.b64encode(batch.container).decode("ascii"),
        "manifest": batch.manifest,
        "replay": replay,
        "accepted_sequence": accepted_sequence,
        "checkpoint_lag_events": max(0, accepted_sequence - batch.sequence_end),
        "plaintext_context_exposed": False,
        "telegram_private_data_exposed": False,
    }


def _ack_payload(
    binding: BetaSpaceBindingModel,
    batch: BetaSpaceCheckpointExportModel,
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
        "last_exported_sequence": binding.checkpoint_last_exported_sequence,
        "checkpoint_lag_events": 0,
        "replay": replay,
        "plaintext_context_exposed": False,
        "telegram_private_data_exposed": False,
    }
