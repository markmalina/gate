"""Read-only legacy parity adapter for canonical Telegram Business text."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.telegram_business_text_parity import (
    TelegramBusinessLegacyTextFact,
    TelegramBusinessTextParityCommand,
    TelegramBusinessTextParityError,
    TelegramBusinessTextParityObserver,
    TelegramBusinessTextParityResult,
    compare_telegram_business_text_parity,
)
from app.models.domain import Account, Message, ReplyTask


class SessionFactoryTelegramBusinessTextParityObserver(
    TelegramBusinessTextParityObserver
):
    """Compare one canonical fact with the already committed compatibility row."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def observe(
        self,
        command: TelegramBusinessTextParityCommand,
    ) -> TelegramBusinessTextParityResult:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        Account.alias,
                        Message.direction,
                        Message.chat_id,
                        Message.business_connection_id,
                        Message.text,
                        func.count(ReplyTask.id),
                    )
                    .join(Account, Account.id == Message.account_id)
                    .outerjoin(
                        ReplyTask,
                        ReplyTask.trigger_message_id == Message.id,
                    )
                    .where(
                        Message.telegram_update_id == command.update_id,
                        Message.source == "telegram",
                        Message.business_connection_id.is_not(None),
                    )
                    .group_by(
                        Account.alias,
                        Message.id,
                        Message.direction,
                        Message.chat_id,
                        Message.business_connection_id,
                        Message.text,
                    )
                )
            ).all()
        if not rows:
            raise TelegramBusinessTextParityError(
                "telegram_business_text_parity_legacy_pending"
            )
        if len(rows) != 1:
            raise TelegramBusinessTextParityError(
                "telegram_business_text_parity_legacy_cardinality"
            )
        (
            account_alias,
            direction,
            provider_dialog_id,
            business_connection_id,
            text_content,
            reply_task_count,
        ) = rows[0]
        if not all(
            isinstance(value, str)
            for value in (
                account_alias,
                direction,
                provider_dialog_id,
                business_connection_id,
                text_content,
            )
        ):
            raise TelegramBusinessTextParityError(
                "telegram_business_text_parity_legacy_invalid"
            )
        return compare_telegram_business_text_parity(
            command,
            TelegramBusinessLegacyTextFact(
                account_alias=account_alias,
                direction=direction,
                provider_dialog_id=provider_dialog_id,
                business_connection_id=business_connection_id,
                text_content=text_content,
                reply_task_count=int(reply_task_count),
            ),
        )
