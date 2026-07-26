"""Durable provider-neutral context handoff from Gate to Beta."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.beta_context import (
    BetaContextBindingModel,
    BetaContextCheckpointStateModel,
    BetaContextEventModel,
    GateContextOutboxModel,
)


@dataclass(frozen=True, slots=True)
class BetaContextBinding:
    installation_id: uuid.UUID
    installation_ref: str
    user_workspace_id: str
    group_id: str


async def load_beta_context_binding(
    session: AsyncSession,
    *,
    installation_id: uuid.UUID,
) -> BetaContextBinding | None:
    row = await session.scalar(
        select(BetaContextBindingModel).where(
            BetaContextBindingModel.installation_id == installation_id,
            BetaContextBindingModel.state == "active",
        )
    )
    if row is None:
        return None
    return BetaContextBinding(
        installation_id=row.installation_id,
        installation_ref=row.installation_ref,
        user_workspace_id=row.user_workspace_id,
        group_id=row.group_id,
    )


def scoped_actor_ref(binding: BetaContextBinding, provider_actor_id: str) -> str:
    digest = hashlib.sha256(
        f"{binding.installation_ref}:{provider_actor_id}".encode()
    ).hexdigest()
    return f"actor-{digest[:32]}"


async def enqueue_context_event(
    session: AsyncSession,
    *,
    binding: BetaContextBinding,
    source_key: str,
    direction: str,
    kind: str,
    occurred_at: datetime,
    actor_ref: str,
    text_value: str,
) -> None:
    content = {
        "format": "structured_text",
        "text": text_value,
        "language": "ru",
    }
    content_sha256 = hashlib.sha256(text_value.encode()).hexdigest()
    source_digest = hashlib.sha256(
        f"{binding.installation_ref}:{source_key}:{direction}".encode()
    ).hexdigest()
    gate_event_ref = f"gate-event-{source_digest}"
    idempotency_key = f"gate.context.{source_digest}"
    await session.execute(
        insert(GateContextOutboxModel)
        .values(
            installation_id=binding.installation_id,
            gate_event_ref=gate_event_ref,
            idempotency_key=idempotency_key,
            direction=direction,
            kind=kind,
            occurred_at=occurred_at,
            actor_ref=actor_ref,
            content=content,
            content_sha256=content_sha256,
            state="pending",
        )
        .on_conflict_do_nothing(
            index_elements=[
                GateContextOutboxModel.installation_id,
                GateContextOutboxModel.idempotency_key,
            ]
        )
    )


class SessionFactoryBetaContextTransfer:
    """Retryable one-batch outbox consumer for the co-located pilot sink."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def drain_once(self, *, limit: int = 50) -> int:
        if limit < 1 or limit > 500:
            raise ValueError("Beta context drain limit must be between 1 and 500")
        async with self._sessions.begin() as session:
            rows = (
                await session.execute(
                    select(GateContextOutboxModel)
                    .where(GateContextOutboxModel.state == "pending")
                    .order_by(
                        GateContextOutboxModel.created_at,
                        GateContextOutboxModel.id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            accepted = 0
            for row in rows:
                binding = await load_beta_context_binding(
                    session,
                    installation_id=row.installation_id,
                )
                if binding is None:
                    row.state = "dead_letter"
                    row.error_code = "beta_context_binding_inactive"
                    row.attempt_count += 1
                    continue
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:group_id))"),
                    {"group_id": binding.group_id},
                )
                existing = await session.scalar(
                    select(BetaContextEventModel).where(
                        BetaContextEventModel.installation_id
                        == row.installation_id,
                        BetaContextEventModel.idempotency_key
                        == row.idempotency_key,
                    )
                )
                if existing is not None:
                    if (
                        existing.source_event_ref != row.gate_event_ref
                        or existing.content_sha256 != row.content_sha256
                    ):
                        row.state = "dead_letter"
                        row.error_code = "beta_context_idempotency_conflict"
                        row.attempt_count += 1
                        continue
                    row.state = "accepted"
                    row.canonical_sequence = existing.sequence
                    row.error_code = None
                    row.attempt_count += 1
                    accepted += 1
                    continue
                last_sequence = await session.scalar(
                    select(func.coalesce(func.max(BetaContextEventModel.sequence), 0))
                    .where(BetaContextEventModel.group_id == binding.group_id)
                )
                sequence = int(last_sequence or 0) + 1
                event_id = f"beta-event-{uuid.uuid5(uuid.NAMESPACE_URL, row.idempotency_key).hex}"
                session.add(
                    BetaContextEventModel(
                        event_id=event_id,
                        installation_id=row.installation_id,
                        group_id=binding.group_id,
                        sequence=sequence,
                        direction=row.direction,
                        kind=row.kind,
                        occurred_at=row.occurred_at,
                        actor_ref=row.actor_ref,
                        content=row.content,
                        source_event_ref=row.gate_event_ref,
                        content_sha256=row.content_sha256,
                        idempotency_key=row.idempotency_key,
                    )
                )
                await session.execute(
                    insert(BetaContextCheckpointStateModel)
                    .values(
                        installation_id=row.installation_id,
                        group_id=binding.group_id,
                        last_sequence=sequence,
                        last_exported_sequence=0,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            BetaContextCheckpointStateModel.installation_id
                        ],
                        set_={
                            "last_sequence": sequence,
                            "updated_at": func.now(),
                        },
                    )
                )
                row.state = "accepted"
                row.canonical_sequence = sequence
                row.error_code = None
                row.attempt_count += 1
                accepted += 1
            await session.flush()
            return accepted


async def mark_context_exported(
    session: AsyncSession,
    *,
    group_id: str,
    sequence: int,
) -> None:
    current = await session.scalar(
        select(BetaContextCheckpointStateModel).where(
            BetaContextCheckpointStateModel.group_id == group_id
        )
    )
    if current is None or sequence < current.last_exported_sequence:
        raise ValueError("Beta checkpoint cursor is invalid")
    await session.execute(
        update(BetaContextCheckpointStateModel)
        .where(BetaContextCheckpointStateModel.group_id == group_id)
        .values(last_exported_sequence=sequence, updated_at=func.now())
    )
