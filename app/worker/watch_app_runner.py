"""One default-off scheduled pass for Watch metadata via GitHub App."""

from __future__ import annotations

import asyncio
import os
import re

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.infrastructure.watch_app_auth import WatchAppInstallation, issue_mark_token
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_voice_early_cards import (
    discover_early_cards,
    drain_saved,
    watch_scope,
)
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.models.watch_voice import WatchEarlyCursor


def app_installation() -> WatchAppInstallation | None:
    app_id = os.getenv("GATE_WATCH_APP_ID", "")
    installation_id = os.getenv("GATE_WATCH_APP_INSTALLATION_ID", "")
    slug = os.getenv("GATE_WATCH_APP_SLUG", "")
    if (
        not re.fullmatch(r"[1-9][0-9]{0,14}", app_id)
        or not re.fullmatch(r"[1-9][0-9]{0,14}", installation_id)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug)
    ):
        return None
    return WatchAppInstallation(int(app_id), int(installation_id), slug)


async def main() -> int:
    if os.getenv("GATE_WATCH_APP_RUNNER_ENABLED", "false").lower() != "true":
        print("watch_app_runner_disabled")
        return 0
    settings = get_settings()
    key = os.getenv("GATE_WATCH_APP_PRIVATE_KEY", "")
    url = os.getenv("GATE_WATCH_APP_DATABASE_URL", "")
    if (
        not key
        or not url
        or settings.gate_workspace_id != "mark-personal"
        or settings.gate_watch_voice_enabled
        or not settings.dropbox_call_account_id
    ):
        print("watch_app_runner_configuration_invalid")
        return 1
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    if not url.startswith("postgresql+asyncpg://"):
        print("watch_app_runner_configuration_invalid")
        return 1
    identity = app_installation()
    if identity is None:
        print("watch_app_runner_configuration_invalid")
        return 1
    limit = os.getenv("GATE_WATCH_APP_BATCH_SIZE", "5")
    if limit not in {str(size) for size in range(1, 11)}:
        print("watch_app_runner_configuration_invalid")
        return 1
    engine = None
    try:
        engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            pool_timeout=5,
            connect_args={"timeout": 5, "command_timeout": 5},
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        key_scope = watch_scope(settings)
        async with factory() as session:
            cursor = await session.get(
                WatchEarlyCursor, (key_scope["workspace_key"], key_scope["account_id"])
            )
            if cursor is None or cursor.writer_mode != "app":
                print("watch_app_runner_writer_inactive")
                return 1
        async with httpx.AsyncClient(timeout=12.0, trust_env=False) as http:
            discovery_failed = False
            try:
                await discover_early_cards(settings, factory, http, writer="app")
            except WatchVoiceError:
                discovery_failed = True
            async with asyncio.timeout(95):
                token = await issue_mark_token(http, key, identity)
                client = AppInboxClient(http, token, app_slug=identity.slug)
                await client.preflight()
                receipt = await drain_saved(factory, client, key_scope, int(limit), writer="app")
        print("watch_app_runner_" + str(receipt["result"]))
        if discovery_failed:
            print("watch_app_runner_discovery_failed")
            return 1
        return 0
    except Exception:
        # Provider errors may contain sensitive URLs and credentials.
        print("watch_app_runner_failed")
        return 1
    finally:
        if engine is not None:
            await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
