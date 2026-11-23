"""One explicitly enabled publication attempt for a scoped Gate stop event."""

import argparse
import asyncio
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.mcp_security import AccessScope, build_mcp_read_principal
from app.infrastructure.database import database_runtime_lifespan
from app.infrastructure.reply_sequence_stop_notice import read_reply_stop_notice
from app.infrastructure.reply_stop_inbox_delivery import (
    ReplyStopDelivery,
    ReplyStopInboxClient,
    deliver_reply_stop_notice,
)
from app.infrastructure.watch_app_auth import (
    AppAuthError,
    WatchAppInstallation,
    issue_mark_token,
)

_PREFIX = "GATE_REPLY_STOP_NOTICE_"


@dataclass(frozen=True, slots=True)
class PublisherConfig:
    identity: WatchAppInstallation
    scope: AccessScope
    private_key: str = field(repr=False)


def publisher_config(environment: Mapping[str, str]) -> PublisherConfig | None:
    if environment.get(_PREFIX + "ENABLED") != "true":
        return None
    app_id = environment.get(_PREFIX + "APP_ID", "")
    installation_id = environment.get(_PREFIX + "APP_INSTALLATION_ID", "")
    slug = environment.get(_PREFIX + "APP_SLUG", "")
    key = environment.get(_PREFIX + "APP_PRIVATE_KEY", "")
    if (
        not re.fullmatch(r"[1-9][0-9]{0,14}", app_id)
        or not re.fullmatch(r"[1-9][0-9]{0,14}", installation_id)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug)
        or not 1 <= len(key) <= 16384
        or any(
            value == environment.get("GATE_WATCH_APP_" + name)
            for name, value in (
                ("ID", app_id), ("INSTALLATION_ID", installation_id), ("SLUG", slug),
            )
        )
    ):
        return None
    try:
        scope = build_mcp_read_principal(
            workspace_id="mark-personal",
            account_grants=environment.get(_PREFIX + "ACCOUNT_IDS", ""),
            project_grants=environment.get(_PREFIX + "PROJECT_IDS", ""),
        ).access_scope
    except ValueError:
        return None
    identity = WatchAppInstallation(int(app_id), int(installation_id), slug)
    return PublisherConfig(identity, scope, key)


async def publish_once(
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    config: PublisherConfig,
    *,
    event_id: UUID,
) -> ReplyStopDelivery:
    async with sessions() as session:
        if await read_reply_stop_notice(session, event_id=event_id, scope=config.scope) is None:
            return ReplyStopDelivery("route_unavailable")
    try:
        token = await issue_mark_token(http, config.private_key, config.identity)
    except AppAuthError:
        return ReplyStopDelivery("route_unavailable")
    return await deliver_reply_stop_notice(
        sessions, event_id=event_id, scope=config.scope,
        inbox=ReplyStopInboxClient(http, token, app_slug=config.identity.slug),
    )


async def run(event_id: UUID, config: PublisherConfig) -> ReplyStopDelivery:
    settings = get_settings()
    if settings.gate_workspace_id != config.scope.workspace_id or not settings.database_url:
        return ReplyStopDelivery("route_unavailable")
    async with database_runtime_lifespan(settings.database_url) as runtime:
        async with httpx.AsyncClient(timeout=12.0, trust_env=False) as http:
            return await publish_once(runtime.session_factory, http, config, event_id=event_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_id", type=UUID)
    event_id = parser.parse_args().event_id
    config = publisher_config(os.environ)
    if config is None:
        print("reply_stop_notice_route_unavailable")
        return 1
    try:
        result = asyncio.run(run(event_id, config))
    except Exception:
        # Keep provider errors, credentials and database URLs out of CLI output.
        print("reply_stop_notice_failed")
        return 1
    print(f"reply_stop_notice_{result.state}")
    return 0 if result.state == "published" else 1


if __name__ == "__main__":
    raise SystemExit(main())
