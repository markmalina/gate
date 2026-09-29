"""Exact owner notification route; no recipient, last-chat or alias fallback."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Account, TelegramBusinessConnection


async def owner_route(
    session: AsyncSession, *, account_id: UUID, principal_subject: str, recipient: str
) -> TelegramBusinessConnection | None:
    account = await session.get(Account, account_id)
    connections = (
        await session.scalars(
            select(TelegramBusinessConnection).where(
                TelegramBusinessConnection.account_id == account_id,
                TelegramBusinessConnection.is_enabled.is_(True),
            )
        )
    ).all()
    route = connections[0] if len(connections) == 1 else None
    if (
        account is None
        or route is None
        or not account.is_active
        or (account.permissions or {}).get("reply_sequence_owner_subject") != principal_subject
        or account.telegram_user_id is None
        or account.telegram_user_id <= 0
        or route.telegram_user_id != account.telegram_user_id
        or route.user_chat_id != account.telegram_user_id
        or str(route.user_chat_id) == recipient
    ):
        return None
    return route
