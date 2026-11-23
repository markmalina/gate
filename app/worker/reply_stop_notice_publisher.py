"""Publish committed Gate stop notices through the dedicated private inbox."""

import argparse
import asyncio
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy import String, cast, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

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
from app.models.domain import Account, AuditLog

_PREFIX = "GATE_REPLY_STOP_NOTICE_"
_PAGE_SIZE = 4
_POLL_SECONDS = 15
_Cursor = tuple[datetime, UUID]
_logger = logging.getLogger(__name__)


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


def automatic_publisher_config(
    environment: Mapping[str, str],
) -> tuple[PublisherConfig, datetime] | None:
    config = publisher_config(environment)
    since = environment.get(_PREFIX + "AUTO_AFTER", "")
    if config is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", since):
        return None
    try:
        return config, datetime.fromisoformat(since).astimezone(UTC)
    except ValueError:
        return None


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


async def publish_pending_page(
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    config: PublisherConfig,
    *,
    since: datetime,
    cursor: _Cursor | None = None,
) -> _Cursor | None:
    """Walk eligible stops in order, and wrap to reconcile unresolved events."""
    outcome = aliased(AuditLog)
    terminal = select(outcome.id).where(
        outcome.action.in_(("reply_stop_notice_published", "reply_stop_notice_rejected")),
        outcome.metadata_["event_id"].as_string() == cast(AuditLog.id, String),
    ).exists()
    statement = (
        select(AuditLog.created_at, AuditLog.id)
        .join(Account, Account.id == AuditLog.account_id)
        .where(
            AuditLog.actor == "gate",
            AuditLog.action == "reply_sequence_stopped",
            AuditLog.created_at >= since,
            AuditLog.account_id.in_(config.scope.account_ids),
            AuditLog.project_id.in_(config.scope.project_ids),
            Account.owner == config.scope.workspace_id,
            Account.is_active.is_(True),
            Account.read_enabled.is_(True),
            AuditLog.metadata_["reason"].as_string() == "context_changed",
            ~terminal,
        )
        .order_by(AuditLog.created_at, AuditLog.id)
        .limit(_PAGE_SIZE)
    )
    if cursor is not None:
        statement = statement.where(tuple_(AuditLog.created_at, AuditLog.id) > cursor)
    async with sessions() as session:
        events = (await session.execute(statement)).all()
    for _, event_id in events:
        try:
            await publish_once(sessions, http, config, event_id=event_id)
        except Exception:
            # Leave database claim and outcome as the sole authority after a failure.
            _logger.warning("reply_stop_notice_event_deferred")
    if len(events) < _PAGE_SIZE:
        return None
    return events[-1][0], events[-1][1]


async def run_automatic_publisher(
    sessions: async_sessionmaker[AsyncSession], config: PublisherConfig, since: datetime,
) -> None:
    cursor: _Cursor | None = None
    async with httpx.AsyncClient(timeout=12.0, trust_env=False) as http:
        while True:
            try:
                cursor = await publish_pending_page(
                    sessions, http, config, since=since, cursor=cursor,
                )
            except Exception:
                _logger.warning("reply_stop_notice_poll_deferred")
            await asyncio.sleep(_POLL_SECONDS)


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
