"""SQL read adapter for Telegram ingress identity shadow parity."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

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
        target_connection = aliased(
            TelegramBusinessConnection,
            name="target_connection",
        )
        target_account = aliased(Account, name="target_account")
        owner_connection = aliased(
            TelegramBusinessConnection,
            name="owner_connection",
        )
        result = await self._session.execute(
            select(
                target_account.id,
                target_account.alias,
                target_account.telegram_user_id,
                owner_connection.telegram_user_id,
                owner_connection.user_chat_id,
            )
            .select_from(target_connection)
            .join(target_account, target_connection.account_id == target_account.id)
            .outerjoin(
                owner_connection,
                owner_connection.account_id == target_account.id,
            )
            .where(
                target_connection.business_connection_id == business_connection_id,
                target_connection.is_enabled.is_(True),
                target_account.is_active.is_(True),
                target_account.owner.op("~")(r"\S"),
            )
        )
        rows = result.all()
        if not rows:
            return None
        account_id, account_alias, _, _, _ = rows[0]
        owner_identity_ids = frozenset(
            str(value)
            for row in rows
            for value in row[2:]
            if value is not None
        )
        return TelegramIngressIdentitySnapshot(
            account_id=str(account_id),
            account_alias=account_alias,
            project_id=str(DEFAULT_PROJECT_ID),
            owner_identity_ids=owner_identity_ids,
        )


class SessionFactoryTelegramIngressIdentityReader:
    """Own one short identity session and release it before queue completion."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        async with self._sessions() as session:
            return await SqlTelegramIngressIdentityReader(session).read(
                business_connection_id=business_connection_id,
            )
