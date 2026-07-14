"""PostgreSQL queue health and abandoned-claim recovery adapters."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.telegram_queue_maintenance import (
    RecoverStaleTelegramClaimsCommand,
    TelegramQueueHealth,
    TelegramQueueHealthReader,
    TelegramQueueMaintenance,
    TelegramStaleClaimRecovery,
)
from app.models.domain import (
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
)


class SqlTelegramQueueHealthReader(TelegramQueueHealthReader):
    """Read aggregate queue health without loading payload columns."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(
        self,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> TelegramQueueHealth:
        status_result = await self._session.execute(
            select(TelegramOutboxEvent.status, func.count())
            .group_by(TelegramOutboxEvent.status)
        )
        counts = {status: count for status, count in status_result.all()}
        stale_processing = await self._session.scalar(
            select(func.count())
            .select_from(TelegramOutboxEvent)
            .where(
                TelegramOutboxEvent.status == TelegramOutboxStatus.PROCESSING.value,
                TelegramOutboxEvent.locked_at <= stale_before,
            )
        )
        oldest_due_at = await self._session.scalar(
            select(func.min(TelegramOutboxEvent.available_at)).where(
                TelegramOutboxEvent.status.in_(
                    {
                        TelegramOutboxStatus.PENDING.value,
                        TelegramOutboxStatus.RETRY_SCHEDULED.value,
                    }
                ),
                TelegramOutboxEvent.available_at <= now,
            )
        )
        return TelegramQueueHealth(
            pending=counts.get(TelegramOutboxStatus.PENDING.value, 0),
            processing=counts.get(TelegramOutboxStatus.PROCESSING.value, 0),
            retry_scheduled=counts.get(
                TelegramOutboxStatus.RETRY_SCHEDULED.value,
                0,
            ),
            delivered=counts.get(TelegramOutboxStatus.DELIVERED.value, 0),
            dead_letter=counts.get(TelegramOutboxStatus.DEAD_LETTER.value, 0),
            stale_processing=stale_processing or 0,
            oldest_due_at=oldest_due_at,
        )


class SqlTelegramQueueMaintenance(TelegramQueueMaintenance):
    """Recover stale processing rows in one bounded transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def recover_stale(
        self,
        command: RecoverStaleTelegramClaimsCommand,
    ) -> TelegramStaleClaimRecovery:
        try:
            result = await self._session.execute(
                select(TelegramOutboxEvent, TelegramReceipt)
                .join(
                    TelegramReceipt,
                    TelegramReceipt.id == TelegramOutboxEvent.receipt_id,
                )
                .where(
                    TelegramOutboxEvent.status
                    == TelegramOutboxStatus.PROCESSING.value,
                    TelegramReceipt.status
                    == TelegramReceiptStatus.PROCESSING.value,
                    TelegramOutboxEvent.locked_at <= command.stale_before,
                )
                .order_by(
                    TelegramOutboxEvent.locked_at,
                    TelegramOutboxEvent.id,
                )
                .limit(command.limit)
                .with_for_update(skip_locked=True)
            )
            rows = result.all()
            retried = 0
            dead_lettered = 0
            for event, receipt in rows:
                inconsistent_attempt = event.attempt_count != receipt.attempt_count
                terminal = (
                    inconsistent_attempt
                    or event.attempt_count >= command.max_attempts
                )
                error_code = (
                    "worker_claim_inconsistent"
                    if inconsistent_attempt
                    else "worker_lease_expired"
                )
                event.last_error_code = error_code
                event.last_error_at = command.recovered_at
                event.locked_at = None
                receipt.last_error_code = error_code
                receipt.last_error_at = command.recovered_at
                receipt.processing_started_at = None

                if terminal:
                    event.status = TelegramOutboxStatus.DEAD_LETTER.value
                    receipt.status = TelegramReceiptStatus.DEAD_LETTER.value
                    receipt.next_attempt_at = None
                    dead_lettered += 1
                else:
                    event.status = TelegramOutboxStatus.RETRY_SCHEDULED.value
                    event.available_at = command.recovered_at
                    receipt.status = TelegramReceiptStatus.RECEIVED.value
                    receipt.next_attempt_at = command.recovered_at
                    retried += 1

            await self._session.commit()
            return TelegramStaleClaimRecovery(
                recovered=len(rows),
                retry_scheduled=retried,
                dead_lettered=dead_lettered,
            )
        except Exception:
            await self._session.rollback()
            raise
