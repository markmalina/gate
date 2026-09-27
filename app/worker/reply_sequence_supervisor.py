"""Bounded local dispatch ownership for #716, pending durable #718 integration.

This module is deliberately not wired to the legacy sequence worker. Callers
must obtain durable claims/permits and perform final sender admission; the
local bound is not a fleet-wide concurrency limit or authorization to send.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DispatchCompletion:
    sequence_id: UUID
    outcome: Literal["finished", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class DispatchShutdown:
    completed: tuple[DispatchCompletion, ...]
    unresolved: tuple[UUID, ...]


def _validate_timeout(value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError("Dispatch timeout must be finite and non-negative")


class ReplySequenceSupervisor:
    """Own at most N dispatch coroutines and reap on *any* completion.

    One event loop owns the instance. A dispatch coroutine owns its fresh DB
    sessions and writes its durable outcome before returning. This supervisor
    handles task lifetime only; failed/cancelled work requires reconciliation,
    never an automatic resend. No provider exception text leaves the pool.
    """

    def __init__(self, *, max_inflight: int) -> None:
        if type(max_inflight) is not int or not 1 <= max_inflight <= 8:
            raise ValueError("Dispatch concurrency must be an integer between 1 and 8")
        self._limit = max_inflight
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._closing = False

    @property
    def available_capacity(self) -> int:
        """Completed tasks retain ownership until their completion is collected."""

        return 0 if self._closing else self._limit - len(self._tasks)

    def try_start(
        self,
        sequence_id: UUID,
        dispatch: Callable[[], Coroutine[Any, Any, None]],
    ) -> bool:
        """Call the factory only when bounded capacity and local ownership permit."""

        if self.available_capacity == 0 or sequence_id in self._tasks:
            return False
        self._tasks[sequence_id] = asyncio.create_task(dispatch())
        return True

    def collect_finished(self) -> tuple[DispatchCompletion, ...]:
        completed: list[DispatchCompletion] = []
        for sequence_id, task in tuple(self._tasks.items()):
            if not task.done():
                continue
            if task.cancelled():
                outcome: Literal["finished", "failed", "cancelled"] = "cancelled"
            elif task.exception() is not None:
                outcome = "failed"
            else:
                outcome = "finished"
            completed.append(DispatchCompletion(sequence_id, outcome))
            del self._tasks[sequence_id]
        return tuple(completed)

    async def wait_for_completion(self, *, timeout: float) -> tuple[DispatchCompletion, ...]:
        """Return after the first completion or poll deadline, not a batch barrier."""

        _validate_timeout(timeout)
        if self._tasks:
            await asyncio.wait(
                self._tasks.values(), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        return self.collect_finished()

    async def shutdown(
        self, *, grace_seconds: float = 20, cancel_seconds: float = 5
    ) -> DispatchShutdown:
        """Stop admission, drain, cancel, and report work that cannot be proven stopped.

        Unresolved tasks remain owned by this object. Integration MUST quarantine
        their durable permits, disable admission, and terminate the process;
        timeout/cancellation alone never proves that HTTP was not performed.
        No callback unlocks or requeues a durable claim here.
        """

        _validate_timeout(grace_seconds)
        _validate_timeout(cancel_seconds)
        self._closing = True
        if self._tasks:
            await asyncio.wait(self._tasks.values(), timeout=grace_seconds)
        completed = self.collect_finished()
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks.values(), timeout=cancel_seconds)
        return DispatchShutdown(
            completed=completed + self.collect_finished(),
            unresolved=tuple(self._tasks),
        )
