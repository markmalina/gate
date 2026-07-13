"""PostgreSQL adapter for atomic Telegram receipt and outbox acceptance."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    TelegramReceiptAcceptance,
    TelegramReceiptStore,
)
from app.models.domain import TelegramOutboxEvent, TelegramReceipt


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
