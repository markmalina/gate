"""Disabled-by-default one-shot runtime for Telegram outbox shadow processing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.telegram_ingress_shadow import (
    build_telegram_ingress_routing_context,
    classify_telegram_ingress,
)
from app.application.telegram_processing import (
    ProcessTelegramOutboxService,
    TelegramProcessingQueue,
    TelegramProcessingWorkItem,
    TelegramUpdateProcessingError,
    TelegramUpdateProcessor,
)
from app.application.telegram_work_ownership_shadow import (
    TelegramConnectorRole,
    evaluate_telegram_work_ownership_shadow,
)
from app.application.work_ownership import DirectionEvidence
from app.core.config import Settings, get_settings
from app.infrastructure.telegram_processing import SqlTelegramProcessingQueue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramWorkerRuntimeResult:
    """Bounded runtime result suitable for redacted operational logs."""

    enabled: bool
    disposition: str
    attempt: int | None = None
    error_code: str | None = None


class ShadowClassifyingTelegramProcessor(TelegramUpdateProcessor):
    """Classify one payload without Telegram, database or workflow side effects."""

    def __init__(self, *, settings: Settings, runtime_logger: logging.Logger) -> None:
        self._settings = settings
        self._logger = runtime_logger

    async def process(self, item: TelegramProcessingWorkItem) -> None:
        if item.payload.get("update_id") != item.update_id:
            raise TelegramUpdateProcessingError("worker_update_id_mismatch")
        try:
            decision = classify_telegram_ingress(item.payload)
            routing = build_telegram_ingress_routing_context(
                item.payload,
                decision=decision,
                settings=self._settings,
            )
            ownership = evaluate_telegram_work_ownership_shadow(
                item.payload,
                decision=decision,
                routing=routing,
                connector_role=TelegramConnectorRole.BUSINESS,
                bot_username=self._settings.telegram_bot_username,
                direction_evidence=DirectionEvidence.UNKNOWN,
            )
        except Exception as exc:
            raise TelegramUpdateProcessingError("shadow_classification_failed") from exc

        self._logger.info(
            "Telegram worker shadow classification: handler=%s reason=%s "
            "account_resolution=%s direction_intent=%s reply_task_intent=%s "
            "ownership_proposed_state=%s ownership_candidate=%s "
            "ownership_reason=%s ownership_signal=%s ownership_requirements=%s "
            "ownership_policy=%s ownership_authoritative=%s",
            decision.handler,
            decision.reason,
            routing.account_resolution,
            routing.direction_intent,
            routing.reply_task_intent,
            ownership.proposed_state.value,
            ownership.candidate_agent.value if ownership.candidate_agent is not None else "none",
            ownership.reason.value,
            ownership.observed_signal.value,
            ",".join(item.value for item in ownership.unresolved_requirements) or "none",
            ownership.policy_version,
            ownership.authoritative,
        )


async def run_telegram_worker_once(
    *,
    settings: Settings,
    session: AsyncSession | None = None,
    queue: TelegramProcessingQueue | None = None,
    processor: TelegramUpdateProcessor | None = None,
    now: datetime | None = None,
    runtime_logger: logging.Logger = logger,
) -> TelegramWorkerRuntimeResult:
    """Run at most one item, failing closed before database access when disabled."""

    if not settings.gate_telegram_worker_enabled:
        return TelegramWorkerRuntimeResult(enabled=False, disposition="disabled")
    if queue is None:
        if session is None:
            raise RuntimeError("An enabled Telegram worker requires a database session")
        queue = SqlTelegramProcessingQueue(session)
    resolved_processor = processor or ShadowClassifyingTelegramProcessor(
        settings=settings,
        runtime_logger=runtime_logger,
    )
    result = await ProcessTelegramOutboxService(
        queue=queue,
        processor=resolved_processor,
    ).run_once(now=now or datetime.now(UTC))
    return TelegramWorkerRuntimeResult(
        enabled=True,
        disposition=result.disposition.value,
        attempt=result.attempt,
        error_code=result.error_code,
    )


async def _run_from_settings(settings: Settings) -> TelegramWorkerRuntimeResult:
    if not settings.gate_telegram_worker_enabled:
        return await run_telegram_worker_once(settings=settings)
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for an enabled Telegram worker")

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            return await run_telegram_worker_once(settings=settings, session=session)
    finally:
        await engine.dispose()


def main() -> None:
    """Run one explicitly invoked iteration; never start a scheduler or loop."""

    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(_run_from_settings(get_settings()))
    logger.info(
        "Telegram worker iteration: enabled=%s disposition=%s attempt=%s "
        "error_code=%s",
        result.enabled,
        result.disposition,
        result.attempt,
        result.error_code,
    )
