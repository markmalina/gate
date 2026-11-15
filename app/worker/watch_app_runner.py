"""One default-off scheduled pass for Watch metadata via GitHub App."""

from __future__ import annotations

import asyncio
import os

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.infrastructure.watch_app_auth import issue_mark_token
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_voice_early_cards import run_early_cards


async def main() -> int:
    if os.getenv("GATE_WATCH_APP_RUNNER_ENABLED", "false").lower() != "true":
        print("watch_app_runner_disabled")
        return 0
    settings = get_settings()
    key = os.getenv("GATE_WATCH_APP_PRIVATE_KEY", "")
    url = os.getenv("GATE_WATCH_APP_DATABASE_URL", "")
    if (not key or not url or settings.gate_workspace_id != "mark-personal"
            or settings.gate_watch_voice_enabled or not settings.dropbox_call_account_id):
        print("watch_app_runner_configuration_invalid")
        return 1
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if not url.startswith("postgresql+asyncpg://"):
        print("watch_app_runner_configuration_invalid")
        return 1
    engine = None
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with httpx.AsyncClient() as http:
            token = await issue_mark_token(http, key)
            client = AppInboxClient(http, token)
            await client.preflight()
            receipt = await run_early_cards(settings, factory, http, client)
        print("watch_app_runner_" + str(receipt.get("result", "invalid")))
        return 0 if receipt.get("result") in {"idle", "processed", "deferred"} else 1
    except Exception:
        # Provider errors may contain sensitive URLs and credentials.
        print("watch_app_runner_failed")
        return 1
    finally:
        if engine is not None:
            await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
