"""Disabled-by-default one-shot runtime for stale approved-send recovery."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.reply_send_recovery import (
    RecoverStaleReplySendsCommand,
    RecoverStaleReplySendsService,
    ReplySendRecovery,
)
from app.core.config import Settings, get_settings
from app.infrastructure.reply_send_recovery import SqlReplySendRecovery

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReplySendRecoveryRuntimeResult:
    """Aggregate recovery outcome safe for operational logs."""

    enabled: bool
    disposition: str
    recovered: int = 0
    sending_marked_unknown: int = 0
    reserved_cancelled: int = 0


async def run_reply_send_recovery_once(
    *,
    settings: Settings,
    session: AsyncSession | None = None,
    recovery: ReplySendRecovery | None = None,
    now: datetime | None = None,
    runtime_logger: logging.Logger = logger,
) -> ReplySendRecoveryRuntimeResult:
    """Run one bounded database-only batch and never schedule another one."""

    if not settings.gate_reply_send_recovery_enabled:
        result = ReplySendRecoveryRuntimeResult(
            enabled=False,
            disposition="disabled",
        )
        _log_result(runtime_logger, result)
        return result

    if recovery is None:
        if session is None:
            raise RuntimeError("An enabled reply-send recovery requires a database session")
        recovery = SqlReplySendRecovery(session)

    recovered_at = now or datetime.now(UTC)
    outcome = await RecoverStaleReplySendsService(recovery).execute(
        RecoverStaleReplySendsCommand(
            stale_before=(
                recovered_at - timedelta(seconds=settings.gate_reply_send_recovery_stale_seconds)
            ),
            recovered_at=recovered_at,
            limit=settings.gate_reply_send_recovery_limit,
        )
    )
    result = ReplySendRecoveryRuntimeResult(
        enabled=True,
        disposition="recovered" if outcome.recovered else "idle",
        recovered=outcome.recovered,
        sending_marked_unknown=outcome.sending_marked_unknown,
        reserved_cancelled=outcome.reserved_cancelled,
    )
    _log_result(runtime_logger, result)
    return result


def _log_result(
    runtime_logger: logging.Logger,
    result: ReplySendRecoveryRuntimeResult,
) -> None:
    runtime_logger.info(
        "Reply-send recovery iteration: enabled=%s disposition=%s recovered=%s "
        "sending_marked_unknown=%s reserved_cancelled=%s",
        result.enabled,
        result.disposition,
        result.recovered,
        result.sending_marked_unknown,
        result.reserved_cancelled,
    )


async def _run_from_settings(settings: Settings) -> ReplySendRecoveryRuntimeResult:
    if not settings.gate_reply_send_recovery_enabled:
        return await run_reply_send_recovery_once(settings=settings)
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for enabled reply-send recovery")

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            return await run_reply_send_recovery_once(
                settings=settings,
                session=session,
            )
    finally:
        await engine.dispose()


def main() -> None:
    """Run one explicitly invoked recovery batch and exit."""

    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_run_from_settings(get_settings()))
    except Exception:
        logger.error("Reply-send recovery failed")
        raise SystemExit(1) from None
