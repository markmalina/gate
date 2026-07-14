"""Disabled-by-default one-shot runtime for Telegram outbox shadow processing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.telegram_ingress_identity import (
    ResolveTelegramIngressIdentityService,
    TelegramIngressIdentityReader,
    TelegramIngressIdentityResolution,
    build_telegram_business_identity_command,
    direction_evidence_from_identity,
    direction_evidence_from_telegram_message,
    not_applicable_identity_resolution,
)
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
    TelegramIngressRoutingView,
    evaluate_telegram_work_ownership_shadow,
)
from app.core.config import Settings, get_settings
from app.core.telegram_connectors import (
    TelegramConnectorResolutionError,
    TelegramConnectorRole,
)
from app.infrastructure.telegram_ingress_identity import (
    SessionFactoryTelegramIngressIdentityReader,
)
from app.infrastructure.telegram_processing import SqlTelegramProcessingQueue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramWorkerRuntimeResult:
    """Bounded runtime result suitable for redacted operational logs."""

    enabled: bool
    disposition: str
    attempt: int | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _IdentityOwnershipRouting:
    """Minimal DB-authoritative routing view for ownership shadowing."""

    account_alias: str | None
    account_resolution: str


class ShadowClassifyingTelegramProcessor(TelegramUpdateProcessor):
    """Classify one payload with SELECT-only identity reads and no external effects."""

    def __init__(
        self,
        *,
        settings: Settings,
        identity_reader: TelegramIngressIdentityReader,
        runtime_logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._connectors = settings.telegram_connectors
        self._identity_reader = identity_reader
        self._logger = runtime_logger

    async def process(self, item: TelegramProcessingWorkItem) -> None:
        if item.payload.get("update_id") != item.update_id:
            raise TelegramUpdateProcessingError("worker_update_id_mismatch")
        try:
            connector = self._connectors.require_worker_connector(item.bot_key)
        except TelegramConnectorResolutionError as exc:
            raise TelegramUpdateProcessingError(exc.code) from None
        try:
            decision = classify_telegram_ingress(item.payload)
            routing = build_telegram_ingress_routing_context(
                item.payload,
                decision=decision,
                settings=self._settings,
            )
        except Exception as exc:
            raise TelegramUpdateProcessingError("shadow_classification_failed") from exc

        identity = not_applicable_identity_resolution()
        identity_reader_used = False
        direction_evidence = direction_evidence_from_telegram_message(item.payload)
        ownership_routing: TelegramIngressRoutingView = routing
        identity_command = (
            build_telegram_business_identity_command(item.payload)
            if connector.role is TelegramConnectorRole.BUSINESS
            and decision.reason == "business_update"
            and decision.command is None
            else None
        )
        if identity_command is not None:
            identity_reader_used = True
            try:
                identity = await ResolveTelegramIngressIdentityService(
                    self._identity_reader
                ).execute(identity_command)
            except Exception:
                raise TelegramUpdateProcessingError("identity_snapshot_failed") from None
            direction_evidence = direction_evidence_from_identity(identity)
            ownership_routing = _identity_ownership_routing(identity)
        elif connector.role is TelegramConnectorRole.BUSINESS:
            ownership_routing = _IdentityOwnershipRouting(
                account_alias=None,
                account_resolution="identity_not_applicable",
            )

        try:
            ownership = evaluate_telegram_work_ownership_shadow(
                item.payload,
                decision=decision,
                routing=ownership_routing,
                connector_role=connector.role,
                bot_username=connector.bot_identity or "",
                direction_evidence=direction_evidence,
            )
        except Exception as exc:
            raise TelegramUpdateProcessingError("shadow_classification_failed") from exc

        self._logger.info(
            "Telegram worker shadow classification: handler=%s reason=%s "
            "configured_account_resolution=%s identity_resolution=%s "
            "identity_reader_used=%s identity_direction=%s "
            "direction_evidence=%s reply_task_intent=%s "
            "ownership_proposed_state=%s ownership_candidate=%s "
            "ownership_reason=%s ownership_signal=%s ownership_requirements=%s "
            "ownership_policy=%s ownership_authoritative=%s",
            decision.handler,
            decision.reason,
            routing.account_resolution,
            identity.resolution.value,
            identity_reader_used,
            identity.direction.value,
            direction_evidence.value,
            identity.reply_task_intent.value,
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
    identity_reader: TelegramIngressIdentityReader | None = None,
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
    if processor is None:
        if identity_reader is None:
            raise RuntimeError(
                "An enabled default Telegram worker requires an identity reader"
            )
        processor = ShadowClassifyingTelegramProcessor(
            settings=settings,
            identity_reader=identity_reader,
            runtime_logger=runtime_logger,
        )
    result = await ProcessTelegramOutboxService(
        queue=queue,
        processor=processor,
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
    identity_reader = SessionFactoryTelegramIngressIdentityReader(sessions)
    try:
        async with sessions() as session:
            return await run_telegram_worker_once(
                settings=settings,
                session=session,
                identity_reader=identity_reader,
            )
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


def _identity_ownership_routing(
    identity: TelegramIngressIdentityResolution,
) -> _IdentityOwnershipRouting:
    return _IdentityOwnershipRouting(
        account_alias=identity.account_alias if identity.resolved else None,
        account_resolution=(
            "database_snapshot" if identity.resolved else identity.resolution.value
        ),
    )
