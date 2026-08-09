"""Retryable drain for durable, content-free Beta checkpoint wake-ups."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.beta_checkpoint_dispatch import (
    BetaCheckpointDispatchEnvelope,
    BetaCheckpointDispatchError,
)
from app.models.beta_space import BetaCheckpointDispatchIntentModel

MAX_BACKOFF_SECONDS = 900
DISPATCH_LEASE = timedelta(minutes=2)
_AMBIGUOUS_CODES = frozenset(
    {
        "beta_checkpoint_dispatch_unavailable",
        "beta_checkpoint_dispatch_response_oversized",
    }
)


class BetaCheckpointDispatcher(Protocol):
    async def dispatch(
        self, *, space_id: str, accepted_sequence: int
    ) -> BetaCheckpointDispatchEnvelope: ...


@dataclass(frozen=True, slots=True)
class BetaCheckpointDispatchDrainResult:
    disposition: str
    space_id: str | None = None
    accepted_sequence: int | None = None
    error_code: str | None = None


class SessionFactoryBetaCheckpointDispatchDrain:
    """Claim one due intent, dispatch outside its transaction, then settle."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        dispatcher: BetaCheckpointDispatcher,
    ) -> None:
        self._sessions = sessions
        self._dispatcher = dispatcher

    async def drain_once(
        self, *, now: datetime | None = None
    ) -> BetaCheckpointDispatchDrainResult:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._sessions.begin() as session:
            intent = await session.scalar(
                select(BetaCheckpointDispatchIntentModel)
                .where(
                    (
                        (
                            BetaCheckpointDispatchIntentModel.state == "pending"
                        )
                        & (
                            BetaCheckpointDispatchIntentModel.next_attempt_at
                            <= observed_at
                        )
                    )
                    | (
                        (
                            BetaCheckpointDispatchIntentModel.state == "sending"
                        )
                        & (
                            BetaCheckpointDispatchIntentModel.lease_expires_at
                            <= observed_at
                        )
                    ),
                    (
                        BetaCheckpointDispatchIntentModel.accepted_sequence
                        > BetaCheckpointDispatchIntentModel.dispatched_sequence
                    )
                    | (
                        BetaCheckpointDispatchIntentModel.wake_generation
                        > BetaCheckpointDispatchIntentModel.dispatched_wake_generation
                    ),
                )
                .order_by(
                    BetaCheckpointDispatchIntentModel.next_attempt_at,
                    BetaCheckpointDispatchIntentModel.updated_at,
                    BetaCheckpointDispatchIntentModel.space_id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if intent is None:
                return BetaCheckpointDispatchDrainResult("idle")
            if intent.state == "sending":
                intent.state = "unknown"
                intent.lease_expires_at = None
                intent.last_error_code = "beta_checkpoint_dispatch_lease_expired"
                intent.settled_at = observed_at
                intent.updated_at = observed_at
                return BetaCheckpointDispatchDrainResult(
                    "unknown",
                    intent.space_id,
                    intent.accepted_sequence,
                    intent.last_error_code,
                )
            if intent.attempt_count >= intent.max_attempts:
                intent.state = "failed"
                intent.lease_expires_at = None
                intent.last_error_code = "beta_checkpoint_dispatch_attempts_exhausted"
                intent.settled_at = observed_at
                intent.updated_at = observed_at
                return BetaCheckpointDispatchDrainResult(
                    "failed",
                    intent.space_id,
                    intent.accepted_sequence,
                    intent.last_error_code,
                )
            intent.state = "sending"
            intent.attempt_count += 1
            intent.lease_expires_at = observed_at + DISPATCH_LEASE
            intent.updated_at = observed_at
            space_id = intent.space_id
            accepted_sequence = intent.accepted_sequence
            wake_generation = intent.wake_generation
            attempt_count = intent.attempt_count

        try:
            envelope = await self._dispatcher.dispatch(
                space_id=space_id,
                accepted_sequence=accepted_sequence,
            )
        except asyncio.CancelledError:
            await self._unknown(
                space_id=space_id,
                accepted_sequence=accepted_sequence,
                error_code="beta_checkpoint_dispatch_cancelled",
                now=observed_at,
            )
            raise
        except BetaCheckpointDispatchError as exc:
            if exc.code in _AMBIGUOUS_CODES:
                await self._unknown(
                    space_id=space_id,
                    accepted_sequence=accepted_sequence,
                    error_code=exc.code,
                    now=observed_at,
                )
                return BetaCheckpointDispatchDrainResult(
                    "unknown", space_id, accepted_sequence, exc.code
                )
            await self._defer(
                space_id=space_id,
                accepted_sequence=accepted_sequence,
                attempt_count=attempt_count,
                error_code=exc.code,
                now=observed_at,
            )
            return BetaCheckpointDispatchDrainResult(
                "deferred", space_id, accepted_sequence, exc.code
            )
        except Exception:
            await self._unknown(
                space_id=space_id,
                accepted_sequence=accepted_sequence,
                error_code="beta_checkpoint_dispatch_unavailable",
                now=observed_at,
            )
            return BetaCheckpointDispatchDrainResult(
                "unknown",
                space_id,
                accepted_sequence,
                "beta_checkpoint_dispatch_unavailable",
            )

        if (
            envelope.space_id != space_id
            or envelope.accepted_sequence != accepted_sequence
        ):
            await self._defer(
                space_id=space_id,
                accepted_sequence=accepted_sequence,
                attempt_count=attempt_count,
                error_code="beta_checkpoint_dispatch_result_mismatch",
                now=observed_at,
            )
            return BetaCheckpointDispatchDrainResult(
                "deferred",
                space_id,
                accepted_sequence,
                "beta_checkpoint_dispatch_result_mismatch",
            )

        async with self._sessions.begin() as session:
            intent = await session.get(
                BetaCheckpointDispatchIntentModel,
                space_id,
                with_for_update=True,
            )
            if intent is None:
                return BetaCheckpointDispatchDrainResult(
                    "missing", space_id, accepted_sequence
                )
            intent.dispatched_sequence = max(
                intent.dispatched_sequence, accepted_sequence
            )
            intent.dispatched_wake_generation = max(
                intent.dispatched_wake_generation,
                wake_generation,
            )
            intent.state = (
                "pending"
                if (
                    intent.accepted_sequence > intent.dispatched_sequence
                    or intent.wake_generation
                    > intent.dispatched_wake_generation
                )
                else "sent"
            )
            intent.last_error_code = None
            intent.next_attempt_at = observed_at
            intent.lease_expires_at = None
            intent.settled_at = (
                observed_at if intent.state == "sent" else None
            )
            intent.updated_at = observed_at
        return BetaCheckpointDispatchDrainResult(
            "sent", space_id, accepted_sequence
        )

    async def _defer(
        self,
        *,
        space_id: str,
        accepted_sequence: int,
        attempt_count: int,
        error_code: str,
        now: datetime,
    ) -> None:
        delay = min(2 ** min(attempt_count, 10), MAX_BACKOFF_SECONDS)
        async with self._sessions.begin() as session:
            intent = await session.get(
                BetaCheckpointDispatchIntentModel,
                space_id,
                with_for_update=True,
            )
            if intent is None:
                return
            intent.state = "pending"
            intent.lease_expires_at = None
            intent.last_error_code = error_code
            intent.next_attempt_at = now + timedelta(seconds=delay)
            intent.updated_at = now

    async def _unknown(
        self,
        *,
        space_id: str,
        accepted_sequence: int,
        error_code: str,
        now: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            intent = await session.get(
                BetaCheckpointDispatchIntentModel,
                space_id,
                with_for_update=True,
            )
            if intent is None or intent.accepted_sequence < accepted_sequence:
                return
            intent.state = "unknown"
            intent.lease_expires_at = None
            intent.last_error_code = error_code
            intent.settled_at = now
            intent.updated_at = now
