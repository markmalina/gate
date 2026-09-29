"""Durable outbound fence even when every sequence feature flag is off.

Baseline databases without canary tables keep their normal transport behavior.
An inaccessible/unknown DB fails closed. No scope is created or inferred here.
"""

import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.reply_sequences import ReplyCanaryScope
from app.services.telegram import TelegramApiError
from app.services.telegram_delivery_gate import CanaryDelivery, canary_intent


class SqlCanaryStartupFence:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], workspace: str) -> None:
        self.sessions = sessions
        self.workspace = workspace

    async def guard_send(self, method: str, intent: CanaryDelivery | None) -> None:
        if intent is not None and intent.execution_id is not None:
            raise TelegramApiError(error_code=403, description="Canary runtime disabled")
        async with self.sessions() as session:
            # Read actual schema, never turn an SQL error into absence of scope.
            exists = await session.scalar(text("SELECT to_regclass('reply_canary_scopes')"))
            if exists is None:
                return
            scope = await session.get(ReplyCanaryScope, self.workspace)
            if scope is not None and scope.state != "closed":
                raise TelegramApiError(error_code=403, description="Canary admission closed")

    async def before(self, payload: dict[str, Any]) -> float:
        await self.guard_send("sendMessage", canary_intent())
        return time.monotonic()

    async def failed(self, payload: dict[str, Any], retry_after: int | None) -> None:
        pass  # Baseline transport retains its existing retry/rate policy.

    async def reserve_canary(self, intent: CanaryDelivery, payload: dict[str, Any]) -> None:
        raise TelegramApiError(error_code=403, description="Canary runtime disabled")
