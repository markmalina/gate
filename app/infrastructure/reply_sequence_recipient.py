"""Bounded recipient labels from the sequence's authorized account/project context."""

import hashlib
import unicodedata
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Conversation, PersonIdentity


def safe_recipient_display(value: str | None, *, account_id: UUID, chat_id: str) -> str:
    cleaned = " ".join(
        "".join(
            c if not unicodedata.category(c).startswith("C") else " " for c in (value or "")
        ).split()
    )[:120]
    if cleaned:
        return cleaned
    opaque = hashlib.sha256(f"{account_id}:{chat_id}".encode()).hexdigest()[:12]
    return f"Адресат без имени · диалог {opaque}"


async def recipient_display(
    session: AsyncSession,
    *,
    account_id: UUID,
    project_id: UUID,
    conversation_id: UUID,
    chat_id: str,
) -> str:
    row = (
        await session.execute(
            select(
                Conversation.title,
                PersonIdentity.metadata_["telegram_contact_name"].as_string(),
                PersonIdentity.display_name,
            )
            .outerjoin(
                PersonIdentity,
                and_(
                    PersonIdentity.account_id == Conversation.account_id,
                    PersonIdentity.platform == "telegram",
                    PersonIdentity.platform_user_id == Conversation.chat_id,
                ),
            )
            .where(
                Conversation.id == conversation_id,
                Conversation.account_id == account_id,
                Conversation.project_id == project_id,
                Conversation.chat_id == chat_id,
            )
        )
    ).one_or_none()
    name = (row[1] or row[2] or row[0]) if row else None
    return safe_recipient_display(name, account_id=account_id, chat_id=chat_id)
