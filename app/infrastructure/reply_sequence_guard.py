"""Dedicated PostgreSQL session advisory barriers for queue, ingress and sender."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

T = TypeVar("T")


class SequenceGuardUnavailable(RuntimeError):
    """No new transport effect is authorized by this guard."""


def dialog_lock_key(workspace: str, account_id: UUID, chat_id: str) -> int:
    value = json.dumps(
        ["gate.reply-dialog.v1", workspace, str(account_id), chat_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big", signed=True)


@dataclass(frozen=True, slots=True)
class DialogLease:
    connection: AsyncConnection
    key: int
    backend_pid: int

    async def check(self) -> None:
        raw = self.key & ((1 << 64) - 1)
        held = await self.connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                "AND pid=pg_backend_pid() AND pid=:pid AND classid=:high "
                "AND objid=:low AND objsubid=1 AND granted)"
            ),
            {"pid": self.backend_pid, "high": raw >> 32, "low": raw & 0xFFFFFFFF},
        )
        await self.connection.commit()
        if not held:
            raise SequenceGuardUnavailable("sequence_guard_unavailable")

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            await self.check()

    async def transport(self, effect: Awaitable[T]) -> T:
        """Observe connection loss while HTTP runs, without an open transaction."""
        send = asyncio.ensure_future(effect)
        monitor = asyncio.create_task(self._monitor())
        try:
            done, _ = await asyncio.wait(
                (send, monitor), timeout=20, return_when=asyncio.FIRST_COMPLETED
            )
            if monitor in done:
                monitor.result()
                raise SequenceGuardUnavailable("sequence_guard_unavailable")
            if send not in done:
                raise TimeoutError("sequence_provider_deadline")
            return send.result()
        finally:
            for task in (send, monitor):
                if not task.done():
                    task.cancel()
            # The production adapter is httpx and cooperates with cancellation.
            # Do not let a task outlive the dialog barrier, even on cancellation.
            await asyncio.gather(send, monitor, return_exceptions=True)


@asynccontextmanager
async def dialog_guard(
    engine: AsyncEngine, *, workspace: str, account_id: UUID, chat_id: str, wait: bool = True
) -> AsyncIterator[DialogLease]:
    key = dialog_lock_key(workspace, account_id, chat_id)
    connection: AsyncConnection | None = None
    acquired = False
    try:
        async with asyncio.timeout(2):
            connection = await engine.connect()
            while True:
                acquired = bool(
                    await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
                )
                await connection.commit()
                if acquired:
                    break
                if not wait:
                    raise SequenceGuardUnavailable("sequence_guard_busy")
                await asyncio.sleep(0.025)
            pid = await connection.scalar(text("SELECT pg_backend_pid()"))
            await connection.commit()
        assert connection is not None and isinstance(pid, int)
        yield DialogLease(connection, key, pid)
    except TimeoutError as exc:
        raise SequenceGuardUnavailable("sequence_guard_unavailable") from exc
    finally:
        if connection is not None:
            try:
                if acquired:
                    await connection.rollback()
                    released = await connection.scalar(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
                    await connection.commit()
                    if not released:
                        await connection.invalidate()
            except BaseException:
                await connection.invalidate()
                raise
            finally:
                await connection.close()
