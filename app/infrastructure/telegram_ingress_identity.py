# mypy: ignore-errors
"""SQL read adapter for Telegram ingress identity shadow parity."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.telegram_ingress_identity import TelegramIngressIdentitySnapshot
from app.models.domain import (
    DEFAULT_PROJECT_ID,
    Account,
    TelegramBusinessConnection,
)


class SqlTelegramIngressIdentityReader:
    """Load identity context through SELECT-only database access."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        connection_result = await self._session.execute(
            select(TelegramBusinessConnection, Account)
            .join(Account, TelegramBusinessConnection.account_id == Account.id)
            .where(
                TelegramBusinessConnection.business_connection_id
                == business_connection_id,
                TelegramBusinessConnection.is_enabled.is_(True),
                Account.is_active.is_(True),
            )
        )
        row = connection_result.one_or_none()
        if row is None:
            return None
        connection, account = row

        identities_result = await self._session.execute(
            select(
                Account.telegram_user_id,
                TelegramBusinessConnection.telegram_user_id,
                TelegramBusinessConnection.user_chat_id,
            )
            .outerjoin(
                TelegramBusinessConnection,
                TelegramBusinessConnection.account_id == Account.id,
            )
            .where(
                Account.owner == account.owner,
                Account.is_active.is_(True),
            )
        )
        owner_identity_ids = frozenset(
            str(value)
            for identity_row in identities_result.all()
            for value in identity_row
            if value is not None
        )
        return TelegramIngressIdentitySnapshot(
            account_id=str(account.id),
            account_alias=account.alias,
            project_id=str(DEFAULT_PROJECT_ID),
            owner_identity_ids=owner_identity_ids,
        )
