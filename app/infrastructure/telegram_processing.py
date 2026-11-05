"""PostgreSQL adapters for durable Telegram receipt and outbox processing."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dialog_snapshots import telegram_dialog_key
from app.application.telegram_processing import (
    ALPHA_GROUP_CAPACITY_ERROR_CODES,
    TELEGRAM_CONTENT_TOMBSTONE_SCHEMA,
    TELEGRAM_PAYLOAD_CANONICALIZATION_VERSION,
    AcceptTelegramReceiptCommand,
    TelegramProcessingDisposition,
    TelegramProcessingPlan,
    TelegramProcessingQueue,
    TelegramProcessingWorkItem,
    TelegramReceiptAcceptance,
    TelegramReceiptError,
    TelegramReceiptStore,
    canonical_telegram_payload_digest,
    is_alpha_membership_update,
)
from app.core.telegram_connectors import TelegramConnectorKey
from app.infrastructure.dialog_snapshots import bump_dialog_version
from app.models.domain import (
    TelegramOutboxEvent,
    TelegramOutboxStatus,
    TelegramReceipt,
    TelegramReceiptStatus,
)


class TelegramQueueStateError(RuntimeError):
    """Reject a stale or inconsistent durable queue transition."""


def _durable_payload_digest(payload: object) -> str:
    if not isinstance(payload, dict):
        raise TelegramReceiptError("telegram_receipt_original_digest_missing")
    if payload.get("schema") != TELEGRAM_CONTENT_TOMBSTONE_SCHEMA:
        return canonical_telegram_payload_digest(payload)
    digest = payload.get("canonical_payload_digest")
    version = payload.get("canonicalization_version")
    if (
        version != TELEGRAM_PAYLOAD_CANONICALIZATION_VERSION
        or not isinstance(digest, str)
        or len(digest) != 64
        or digest != digest.lower()
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise TelegramReceiptError("telegram_receipt_original_digest_missing")
    return digest


class SqlTelegramReceiptStore(TelegramReceiptStore):
    """Create one receipt and one outbox event in the same transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_if_absent(
        self,
        command: AcceptTelegramReceiptCommand,
    ) -> TelegramReceiptAcceptance:
        incoming_digest = canonical_telegram_payload_digest(command.payload)
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
                    select(
                        TelegramReceipt.id,
                        TelegramOutboxEvent.id,
                        TelegramReceipt.payload,
                    )
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
                if _durable_payload_digest(existing[2]) != incoming_digest:
                    raise TelegramReceiptError(
                        "telegram_receipt_mutated_replay"
                    )
                await self._session.commit()
                return TelegramReceiptAcceptance(
                    receipt_id=str(existing[0]),
                    outbox_event_id=str(existing[1]),
                    duplicate=True,
                )

            dialog_key = telegram_dialog_key(command.payload)
            if command.bot_key == TelegramConnectorKey.BUSINESS.value and dialog_key:
                await bump_dialog_version(
                    self._session, connection_id=dialog_key[0], chat_id=dialog_key[1]
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


class SqlAlphaCapacityDeadLetterReplayer:
    """Atomically requeue only exact Alpha membership capacity failures."""

    _SCAN_MULTIPLIER = 8

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def requeue(
        self,
        *,
        bot_key: str,
        now: datetime,
        limit: int,
    ) -> int:
        if not 1 <= limit <= 32:
            raise ValueError("replay limit must be between 1 and 32")
        if not bot_key.strip():
            raise ValueError("bot_key is required")

        try:
            result = await self._session.execute(
                select(TelegramOutboxEvent, TelegramReceipt)
                .join(
                    TelegramReceipt,
                    TelegramReceipt.id == TelegramOutboxEvent.receipt_id,
                )
                .where(
                    TelegramReceipt.bot_key == bot_key,
                    TelegramReceipt.status == TelegramReceiptStatus.DEAD_LETTER.value,
                    TelegramReceipt.last_error_code.in_(
                        ALPHA_GROUP_CAPACITY_ERROR_CODES
                    ),
                    TelegramOutboxEvent.status
                    == TelegramOutboxStatus.DEAD_LETTER.value,
                    TelegramOutboxEvent.last_error_code
                    == TelegramReceipt.last_error_code,
                )
                .order_by(
                    TelegramReceipt.received_at,
                    TelegramReceipt.created_at,
                    TelegramReceipt.id,
                )
                .limit(limit * self._SCAN_MULTIPLIER)
                .with_for_update(skip_locked=True)
            )
            requeued = 0
            for event, receipt in result.all():
                if not is_alpha_membership_update(
                    receipt.payload,
                    update_id=receipt.update_id,
                ):
                    continue

                event.status = TelegramOutboxStatus.PENDING.value
                event.attempt_count = 0
                event.available_at = now
                event.locked_at = None
                event.delivered_at = None
                event.last_error_code = None
                event.last_error_at = None

                receipt.status = TelegramReceiptStatus.RECEIVED.value
                receipt.attempt_count = 0
                receipt.processing_started_at = None
                receipt.processed_at = None
                receipt.next_attempt_at = None
                receipt.last_error_code = None
                receipt.last_error_at = None

                requeued += 1
                if requeued == limit:
                    break

            await self._session.commit()
            return requeued
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
