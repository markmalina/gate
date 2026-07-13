"""PostgreSQL adapters for durable Telegram receipt and outbox processing."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramProcessingDisposition,
    TelegramProcessingPlan,
    TelegramProcessingQueue,
    TelegramProcessingWorkItem,
    TelegramReceiptAcceptance,
    TelegramReceiptStore,
)
from app.models.domain import (
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
)


class TelegramQueueStateError(RuntimeError):
    """Reject a stale or inconsistent durable queue transition."""


class SqlTelegramReceiptStore(TelegramReceiptStore):
    """Create one receipt and one outbox event in the same transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_if_absent(
        self,
        command: AcceptTelegramReceiptCommand,
    ) -> TelegramReceiptAcceptance:
        receipt_id = uuid4()
        outbox_event_id = uuid4()
        try:
            inserted_receipt_id = await self._session.scalar(
                insert(TelegramReceipt)
                .values(
                    id=receipt_id,
                    bot_key=command.bot_key,
                    update_id=command.update_id,
                    payload=command.payload,
                    received_at=command.received_at,
                )
                .on_conflict_do_nothing(
                    index_elements=["bot_key", "update_id"],
                )
                .returning(TelegramReceipt.id)
            )
            if inserted_receipt_id is None:
                existing_result = await self._session.execute(
                    select(TelegramReceipt.id, TelegramOutboxEvent.id)
                    .join(
                        TelegramOutboxEvent,
                        TelegramOutboxEvent.receipt_id == TelegramReceipt.id,
                    )
                    .where(
                        TelegramReceipt.bot_key == command.bot_key,
                        TelegramReceipt.update_id == command.update_id,
                    )
                )
                existing = existing_result.one()
                await self._session.commit()
                return TelegramReceiptAcceptance(
                    receipt_id=str(existing[0]),
                    outbox_event_id=str(existing[1]),
                    duplicate=True,
                )

            await self._session.execute(
                insert(TelegramOutboxEvent).values(
                    id=outbox_event_id,
                    receipt_id=inserted_receipt_id,
                    available_at=command.received_at,
                )
            )
            await self._session.commit()
            return TelegramReceiptAcceptance(
                receipt_id=str(inserted_receipt_id),
                outbox_event_id=str(outbox_event_id),
                duplicate=False,
            )
        except Exception:
            await self._session.rollback()
            raise


class SqlTelegramProcessingQueue(TelegramProcessingQueue):
    """Claim and transition durable work with row-level exclusion."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_due(self, *, now: datetime) -> TelegramProcessingWorkItem | None:
        try:
            result = await self._session.execute(
                select(TelegramOutboxEvent, TelegramReceipt)
                .join(
                    TelegramReceipt,
                    TelegramReceipt.id == TelegramOutboxEvent.receipt_id,
                )
                .where(
                    TelegramOutboxEvent.status.in_(
                        {
                            TelegramOutboxStatus.PENDING.value,
                            TelegramOutboxStatus.RETRY_SCHEDULED.value,
                        }
                    ),
                    TelegramOutboxEvent.available_at <= now,
                    TelegramReceipt.status == TelegramReceiptStatus.RECEIVED.value,
                )
                .order_by(
                    TelegramOutboxEvent.available_at,
                    TelegramOutboxEvent.created_at,
                    TelegramOutboxEvent.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = result.one_or_none()
            if row is None:
                await self._session.rollback()
                return None

            event, receipt = row
            attempt = event.attempt_count + 1
            event.status = TelegramOutboxStatus.PROCESSING.value
            event.attempt_count = attempt
            event.locked_at = now
            receipt.status = TelegramReceiptStatus.PROCESSING.value
            receipt.attempt_count = attempt
            receipt.processing_started_at = now
            receipt.next_attempt_at = None
            await self._session.commit()
            return TelegramProcessingWorkItem(
                receipt_id=str(receipt.id),
                outbox_event_id=str(event.id),
                bot_key=receipt.bot_key,
                update_id=receipt.update_id,
                payload=receipt.payload,
                received_at=receipt.received_at,
                attempt=attempt,
            )
        except Exception:
            await self._session.rollback()
            raise

    async def mark_processed(
        self,
        item: TelegramProcessingWorkItem,
        *,
        processed_at: datetime,
    ) -> None:
        try:
            event, receipt = await self._load_claimed(item)
            event.status = TelegramOutboxStatus.DELIVERED.value
            event.delivered_at = processed_at
            event.locked_at = None
            receipt.status = TelegramReceiptStatus.PROCESSED.value
            receipt.processed_at = processed_at
            receipt.processing_started_at = None
            receipt.next_attempt_at = None
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

    async def record_failure(
        self,
        item: TelegramProcessingWorkItem,
        *,
        failed_at: datetime,
        plan: TelegramProcessingPlan,
    ) -> None:
        if plan.attempt != item.attempt:
            raise TelegramQueueStateError("failure plan attempt does not match claim")
        try:
            event, receipt = await self._load_claimed(item)
            event.last_error_code = plan.error_code
            event.last_error_at = failed_at
            event.locked_at = None
            receipt.last_error_code = plan.error_code
            receipt.last_error_at = failed_at
            receipt.processing_started_at = None

            if plan.disposition == TelegramProcessingDisposition.RETRY:
                if plan.next_attempt_at is None:
                    raise TelegramQueueStateError("retry requires next_attempt_at")
                event.status = TelegramOutboxStatus.RETRY_SCHEDULED.value
                event.available_at = plan.next_attempt_at
                receipt.status = TelegramReceiptStatus.RECEIVED.value
                receipt.next_attempt_at = plan.next_attempt_at
            else:
                event.status = TelegramOutboxStatus.DEAD_LETTER.value
                receipt.status = TelegramReceiptStatus.DEAD_LETTER.value
                receipt.next_attempt_at = None

            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

    async def _load_claimed(
        self,
        item: TelegramProcessingWorkItem,
    ) -> tuple[TelegramOutboxEvent, TelegramReceipt]:
        result = await self._session.execute(
            select(TelegramOutboxEvent, TelegramReceipt)
            .join(
                TelegramReceipt,
                TelegramReceipt.id == TelegramOutboxEvent.receipt_id,
            )
            .where(
                TelegramOutboxEvent.id == UUID(item.outbox_event_id),
                TelegramReceipt.id == UUID(item.receipt_id),
            )
            .with_for_update()
        )
        row = result.one_or_none()
        if row is None:
            raise TelegramQueueStateError("claimed Telegram work item not found")
        event, receipt = row
        if (
            event.status != TelegramOutboxStatus.PROCESSING.value
            or receipt.status != TelegramReceiptStatus.PROCESSING.value
            or event.attempt_count != item.attempt
            or receipt.attempt_count != item.attempt
        ):
            raise TelegramQueueStateError("claimed Telegram work item is stale")
        return event, receipt
