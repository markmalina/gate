"""Durable shared bot budget for manual/document/notification send paths."""

import time
from datetime import timedelta
from typing import Any
from uuid import uuid4

from app.application.reply_sequence_cooldown import (
    ReplySequenceCooldown,
    extend_reply_sequence_cooldown,
)
from app.infrastructure.reply_sequence_canary import (
    CanaryAttemptRequest,
    CanaryDenied,
    reserve_canary_attempt,
)
from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime
from app.models.domain import TelegramBusinessConnection
from app.models.reply_sequences import ReplyCanaryScope, ReplyTransportBudget
from app.services.telegram import TelegramApiError
from app.services.telegram_delivery_gate import CanaryDelivery
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert


class SqlTelegramTransportBudget:
    def __init__(self, runtime: ReplySequenceRuntime) -> None:
        self.runtime = runtime

    async def reserve_canary(self, intent: CanaryDelivery, payload: dict[str, Any]) -> None:
        if not intent.matches(payload):
            raise TelegramApiError(error_code=403, description="Canary payload mismatch")
        async with self.runtime.sessions() as session:
            scope = await session.get(ReplyCanaryScope, self.runtime.workspace)
            if scope is None:
                raise TelegramApiError(error_code=403, description="Canary admission closed")
            request = CanaryAttemptRequest(
                workspace_ref=self.runtime.workspace,
                execution_id=scope.execution_id,
                attempt_key=uuid4(),
                kind=intent.kind,
                principal_subject=intent.principal_subject,
                account_id=intent.account_id,
                dialog_ref=intent.dialog_ref,
                body_sha256=intent.body_sha256,
                notification_destination=(
                    intent.destination if intent.kind == "notification" else None
                ),
                notification_template=intent.notification_template,
            )
            try:
                await reserve_canary_attempt(session, request)
            except CanaryDenied:
                raise TelegramApiError(
                    error_code=403, description="Canary admission closed"
                ) from None

    async def _keys(self, payload: dict[str, Any]) -> list[tuple[str, float]]:
        connection_id = payload.get("business_connection_id")
        if isinstance(connection_id, str):
            async with self.runtime.sessions() as session:
                account = await session.scalar(
                    select(TelegramBusinessConnection.account_id).where(
                        TelegramBusinessConnection.business_connection_id == connection_id
                    )
                )
                if account is None:
                    raise TelegramApiError(
                        error_code=429,
                        description="Transport binding unavailable",
                        retry_after_seconds=60,
                    )
                return self.runtime.resource_keys(
                    account=account, chat=str(payload.get("chat_id", ""))
                )
        return [("bot:" + self.runtime.bot_resource, 0.25)]

    async def before(self, payload: dict[str, Any]) -> float:
        keys = await self._keys(payload)
        async with self.runtime.sessions() as session:
            now = await self.runtime.db_now(session)
            reserved = []
            for key, gap in keys:
                await session.execute(
                    insert(ReplyTransportBudget)
                    .values(resource_key=key, next_allowed_at=now, blocked_indefinitely=False)
                    .on_conflict_do_nothing()
                )
                budget = await session.scalar(
                    select(ReplyTransportBudget)
                    .where(ReplyTransportBudget.resource_key == key)
                    .with_for_update()
                )
                assert budget is not None
                if (
                    budget.blocked_indefinitely
                    or budget.next_allowed_at > now
                    or (budget.blocked_until is not None and budget.blocked_until > now)
                ):
                    raise TelegramApiError(
                        error_code=429,
                        description="Shared transport budget is busy",
                        retry_after_seconds=60,
                    )
                reserved.append((budget, gap))
            now = await self.runtime.db_now(session)
            for budget, gap in reserved:
                budget.next_allowed_at = now + timedelta(seconds=gap + 0.1)
            admitted = time.monotonic()
            await session.commit()
            return admitted

    async def failed(self, payload: dict[str, Any], retry_after: int | None) -> None:
        keys = await self._keys(payload)
        async with self.runtime.sessions() as session:
            now = await self.runtime.db_now(session)
            for key, _ in keys:
                await session.execute(
                    insert(ReplyTransportBudget)
                    .values(resource_key=key, next_allowed_at=now, blocked_indefinitely=False)
                    .on_conflict_do_nothing()
                )
                budget = await session.scalar(
                    select(ReplyTransportBudget)
                    .where(ReplyTransportBudget.resource_key == key)
                    .with_for_update()
                )
                assert budget is not None
                cooldown = extend_reply_sequence_cooldown(
                    now=now,
                    retry_after_seconds=retry_after,
                    current=ReplySequenceCooldown(
                        budget.blocked_until, budget.blocked_indefinitely
                    ),
                )
                budget.blocked_until = cooldown.blocked_until
                budget.blocked_indefinitely = cooldown.blocked_indefinitely
            await session.commit()
