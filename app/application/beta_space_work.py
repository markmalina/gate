"""Bounded lease and retry vocabulary for post-admission Beta Space work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol


class BetaSpaceWorkDisposition(StrEnum):
    RETRY = "retry"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BetaSpaceWorkItem:
    work_id: str
    receipt_id: str = field(repr=False)
    kind: str
    attempt: int
    max_attempts: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class BetaSpaceWorkFailurePlan:
    disposition: BetaSpaceWorkDisposition
    next_attempt_at: datetime | None
    error_code: str


@dataclass(frozen=True, slots=True)
class BetaSpaceWorkRetryPolicy:
    base_delay_seconds: int = 5
    max_delay_seconds: int = 300

    def plan(
        self, *, item: BetaSpaceWorkItem, failed_at: datetime, error_code: str
    ) -> BetaSpaceWorkFailurePlan:
        safe_code = error_code.strip()[:128] or "beta_space_work_failed"
        if item.attempt >= item.max_attempts:
            return BetaSpaceWorkFailurePlan(
                BetaSpaceWorkDisposition.FAILED,
                None,
                safe_code,
            )
        delay = min(
            self.base_delay_seconds * (2 ** (item.attempt - 1)),
            self.max_delay_seconds,
        )
        return BetaSpaceWorkFailurePlan(
            BetaSpaceWorkDisposition.RETRY,
            failed_at + timedelta(seconds=delay),
            safe_code,
        )


class BetaSpaceWorkQueue(Protocol):
    async def claim_due(
        self, *, now: datetime, lease_seconds: int
    ) -> BetaSpaceWorkItem | None: ...

    async def complete(
        self, *, item: BetaSpaceWorkItem, now: datetime
    ) -> None: ...

    async def mark_effect_started(
        self, *, item: BetaSpaceWorkItem, now: datetime
    ) -> None: ...

    async def mark_unknown(
        self, *, item: BetaSpaceWorkItem, now: datetime, error_code: str
    ) -> None: ...

    async def fail(
        self,
        *,
        item: BetaSpaceWorkItem,
        now: datetime,
        plan: BetaSpaceWorkFailurePlan,
    ) -> None: ...
