"""Delete expired staged Business document bytes without retrying sends."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.reply_documents import prune_expired_reply_documents
from app.infrastructure.reply_media import prune_expired_reply_media

logger = logging.getLogger(__name__)


async def run_reply_document_cleanup_loop(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Prune staged bytes even after new document sends are disabled."""

    while True:
        try:
            async with sessions() as session:
                count = await prune_expired_reply_documents(session)
                media_count = await prune_expired_reply_media(session)
                await session.commit()
            if count:
                logger.info("Expired reply document bytes cleared: %s", count)
            if media_count:
                logger.info("Expired reply media bytes cleared: %s", media_count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reply document cleanup failed; will retry at next interval")
        await asyncio.sleep(3600)
