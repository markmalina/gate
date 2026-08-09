"""PostgreSQL lease queue for durable Beta Space continuations."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import cast

from app.application.beta_space_work import (
    BetaSpaceWorkDisposition,
    BetaSpaceWorkFailurePlan,
    BetaSpaceWorkItem,
)
from app.models.beta_material import BetaMaterialDeliveryModel, BetaMaterialJobModel
from app.models.beta_space import (
    BetaConsentDeliveryModel,
    BetaSpaceReplyModel,
    BetaSpaceWorkItemModel,
)
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class BetaSpaceWorkStateError(RuntimeError):
    """Reject a stale owner or inconsistent work transition."""


class SqlBetaSpaceWorkQueue:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_due(
        self, *, now: datetime, lease_seconds: int
    ) -> BetaSpaceWorkItem | None:
        if not 1 <= lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 1 and 900")
        row = cast(
            BetaSpaceWorkItemModel | None,
            await self._session.scalar(
                select(BetaSpaceWorkItemModel)
                .where(
                    or_(
                        and_(
                            BetaSpaceWorkItemModel.state.in_(("pending", "retry")),
                            BetaSpaceWorkItemModel.next_attempt_at <= now,
                        ),
                        and_(
                            BetaSpaceWorkItemModel.state == "leased",
                            BetaSpaceWorkItemModel.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(
                    BetaSpaceWorkItemModel.next_attempt_at,
                    BetaSpaceWorkItemModel.created_at,
                    BetaSpaceWorkItemModel.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            ),
        )
        if row is None:
            await self._session.rollback()
            return None
        if row.state == "leased" and await self._settle_ambiguous(row, now=now):
            await self._session.commit()
            return None
        if row.attempt_count >= row.max_attempts:
            row.state = "failed"
            row.lease_expires_at = None
            row.last_error_code = "beta_space_work_attempts_exhausted"
            row.settled_at = now
            row.updated_at = now
            await self._session.commit()
            return None
        row.state = "leased"
        row.attempt_count += 1
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.last_error_code = None
        row.updated_at = now
        await self._session.commit()
        lease_expires_at = row.lease_expires_at
        if lease_expires_at is None:
            raise BetaSpaceWorkStateError("beta_space_work_lease_missing")
        return BetaSpaceWorkItem(
            work_id=str(row.id),
            receipt_id=str(row.receipt_id),
            kind=row.kind,
            attempt=row.attempt_count,
            max_attempts=row.max_attempts,
            lease_expires_at=lease_expires_at,
        )

    async def mark_effect_started(
        self, *, item: BetaSpaceWorkItem, now: datetime
    ) -> None:
        row = await self._locked(item)
        row.last_error_code = "beta_space_effect_started"
        row.updated_at = now
        await self._session.commit()

    async def complete(self, *, item: BetaSpaceWorkItem, now: datetime) -> None:
        row = await self._locked(item)
        row.state = "completed"
        row.lease_expires_at = None
        row.settled_at = now
        row.updated_at = now
        await self._session.commit()

    async def mark_unknown(
        self, *, item: BetaSpaceWorkItem, now: datetime, error_code: str
    ) -> None:
        row = await self._locked(item)
        row.state = "unknown"
        row.lease_expires_at = None
        row.last_error_code = error_code.strip()[:128] or "beta_space_effect_unknown"
        row.settled_at = now
        row.updated_at = now
        await self._session.commit()

    async def fail(
        self,
        *,
        item: BetaSpaceWorkItem,
        now: datetime,
        plan: BetaSpaceWorkFailurePlan,
    ) -> None:
        row = await self._locked(item)
        row.lease_expires_at = None
        row.last_error_code = plan.error_code
        row.updated_at = now
        if plan.disposition is BetaSpaceWorkDisposition.RETRY:
            if plan.next_attempt_at is None:
                raise BetaSpaceWorkStateError("retry requires next_attempt_at")
            row.state = "retry"
            row.next_attempt_at = plan.next_attempt_at
        else:
            row.state = "failed"
            row.settled_at = now
        await self._session.commit()

    async def _locked(self, item: BetaSpaceWorkItem) -> BetaSpaceWorkItemModel:
        row = cast(
            BetaSpaceWorkItemModel | None,
            await self._session.scalar(
                select(BetaSpaceWorkItemModel)
                .where(BetaSpaceWorkItemModel.id == uuid.UUID(item.work_id))
                .with_for_update()
            ),
        )
        if (
            row is None
            or row.state != "leased"
            or row.attempt_count != item.attempt
            or row.lease_expires_at != item.lease_expires_at
        ):
            await self._session.rollback()
            raise BetaSpaceWorkStateError("beta_space_work_claim_stale")
        return row

    async def _settle_ambiguous(
        self, row: BetaSpaceWorkItemModel, *, now: datetime
    ) -> bool:
        """Fail closed if a crashed lease crossed an outbound send boundary."""

        ambiguous = False
        if row.kind == "reply" and row.event_id is not None:
            reply = await self._session.scalar(
                select(BetaSpaceReplyModel)
                .where(BetaSpaceReplyModel.event_id == row.event_id)
                .with_for_update()
            )
            if reply is not None and reply.state == "sending":
                reply.state = "unknown"
                reply.error_code = "beta_wedding_delivery_lease_expired"
                reply.settled_at = now
                reply.updated_at = now
                ambiguous = True
        elif row.kind == "material" and row.event_id is not None:
            job = await self._session.scalar(
                select(BetaMaterialJobModel)
                .where(BetaMaterialJobModel.event_id == row.event_id)
                .with_for_update()
            )
            if job is not None:
                delivery = await self._session.scalar(
                    select(BetaMaterialDeliveryModel)
                    .where(
                        BetaMaterialDeliveryModel.job_id == job.id,
                        BetaMaterialDeliveryModel.state == "sending",
                    )
                    .order_by(BetaMaterialDeliveryModel.created_at.desc())
                    .limit(1)
                    .with_for_update()
                )
                if delivery is not None:
                    delivery.state = "unknown"
                    delivery.error_code = "beta_material_delivery_lease_expired"
                    delivery.settled_at = now
                    delivery.updated_at = now
                    ambiguous = True
        elif row.kind == "consent_delivery" and row.event_id is not None:
            delivery = await self._session.scalar(
                select(BetaConsentDeliveryModel)
                .where(BetaConsentDeliveryModel.event_id == row.event_id)
                .with_for_update()
            )
            if delivery is not None and delivery.state == "reserved":
                delivery.state = "unknown"
                delivery.settled_at = now
                delivery.updated_at = now
                ambiguous = True
        elif (
            row.kind == "callback_delivery"
            and row.last_error_code == "beta_space_effect_started"
        ):
            # Callback acknowledgement and markup edit are both external sends.
            ambiguous = True
        if ambiguous:
            row.state = "unknown"
            row.lease_expires_at = None
            row.last_error_code = "beta_space_effect_lease_expired"
            row.settled_at = now
            row.updated_at = now
        return ambiguous


class SessionFactoryBetaSpaceWorkQueue:
    """Use a fresh transaction for every durable ownership transition."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim_due(
        self, *, now: datetime, lease_seconds: int
    ) -> BetaSpaceWorkItem | None:
        async with self._sessions() as session:
            return await SqlBetaSpaceWorkQueue(session).claim_due(
                now=now,
                lease_seconds=lease_seconds,
            )

    async def complete(self, *, item: BetaSpaceWorkItem, now: datetime) -> None:
        async with self._sessions() as session:
            await SqlBetaSpaceWorkQueue(session).complete(item=item, now=now)

    async def mark_effect_started(
        self, *, item: BetaSpaceWorkItem, now: datetime
    ) -> None:
        async with self._sessions() as session:
            await SqlBetaSpaceWorkQueue(session).mark_effect_started(
                item=item,
                now=now,
            )

    async def mark_unknown(
        self, *, item: BetaSpaceWorkItem, now: datetime, error_code: str
    ) -> None:
        async with self._sessions() as session:
            await SqlBetaSpaceWorkQueue(session).mark_unknown(
                item=item,
                now=now,
                error_code=error_code,
            )

    async def fail(
        self,
        *,
        item: BetaSpaceWorkItem,
        now: datetime,
        plan: BetaSpaceWorkFailurePlan,
    ) -> None:
        async with self._sessions() as session:
            await SqlBetaSpaceWorkQueue(session).fail(
                item=item,
                now=now,
                plan=plan,
            )
