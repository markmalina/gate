"""Owner-only maintenance entry point for the BECOME Pilot Slice 1."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.become_transcript_review import (
    DeliverPilotTranscriptReview,
)
from app.core.config import get_settings
from app.infrastructure.become_media_intake import (
    SessionFactoryBecomeMediaRetention,
)
from app.infrastructure.become_transcript_review import (
    SessionFactoryPilotTranscriptReviewStore,
)
from app.services.telegram import TelegramClient, build_telegram_http_client


async def _run(command: str) -> dict[str, object]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionFactoryPilotTranscriptReviewStore(sessions)
    try:
        if command == "status":
            return {
                "schema": "gate.become-pilot-slice1-status@1",
                "state": await store.status(),
                "content_exposed": False,
            }
        if command == "purge":
            now = datetime.now(UTC)
            media = await SessionFactoryBecomeMediaRetention(
                sessions
            ).purge_expired(now=now)
            review = await store.purge_expired(now=now)
            return {
                "schema": "gate.become-pilot-slice1-purge@1",
                "raw_purged": media.raw_purged,
                "transcripts_purged": media.transcripts_purged,
                **review,
                "content_exposed": False,
            }
        if command == "deliver":
            if not settings.gate_become_pilot_slice1_enabled:
                raise RuntimeError("BECOME Pilot Slice 1 kill switch is off")
            async with build_telegram_http_client() as http:
                telegram = TelegramClient(
                    credential=(
                        settings.telegram_become_bot_token.get_secret_value()
                    ),
                    http_client=http,
                )
                disposition = await DeliverPilotTranscriptReview(
                    store=store,
                    telegram=telegram,
                    enabled=True,
                    max_operations=(
                        settings.gate_become_pilot_slice1_max_operations
                    ),
                ).run_once(now=datetime.now(UTC))
            return {
                "schema": "gate.become-pilot-slice1-delivery@1",
                "disposition": disposition,
                "content_exposed": False,
            }
        raise RuntimeError("unknown command")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Content-free BECOME Pilot maintenance"
    )
    parser.add_argument(
        "command",
        choices=("status", "purge", "deliver"),
    )
    args = parser.parse_args()
    result = asyncio.run(_run(args.command))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
