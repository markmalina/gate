"""Disabled-by-default runtime for durable Telegram outbox processing."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.alpha_canary import (
    AlphaCanaryDisposition,
    AlphaCanaryError,
    ProcessAlphaCanaryUpdateCommand,
    ProcessAlphaCanaryUpdateService,
)
from app.application.alpha_delivery import DeliverAlphaGrantService
from app.application.alpha_shadow_worker import ProcessAlphaShadowMessageService
from app.application.canonical_messaging import CanonicalIngressStore, ConnectorKind
from app.application.instruction_bundles import DeployedInstructionBundleLoader
from app.application.universal_mayak_demo import ProcessUniversalMayakDemoService
from app.application.universal_mayak_private_ingress import (
    PrivateIngressDisposition,
    ProcessUniversalMayakPrivateMessageCommand,
    ProcessUniversalMayakPrivateMessageService,
)
from app.application.telegram_business_text_inbox import (
    TelegramBusinessTextInboxProjector,
    build_telegram_business_text_inbox_command,
    is_telegram_business_text_cutover_update,
)
from app.application.telegram_business_text_parity import (
    TelegramBusinessTextParityCommand,
    TelegramBusinessTextParityError,
    TelegramBusinessTextParityObserver,
)
from app.application.telegram_canonical_projection import (
    TelegramCanonicalProjectionError,
    project_telegram_canonical_message,
)
from app.application.telegram_ingress_identity import (
    ResolveTelegramIngressIdentityService,
    TelegramIngressDirection,
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
    TelegramUpdateDeferred,
    TelegramUpdateProcessingError,
    TelegramUpdateProcessor,
)
from app.application.telegram_voice_archive import (
    ArchiveTelegramVoiceCommand,
    ArchiveTelegramVoiceService,
    PersistTelegramVoiceTranscriptCommand,
    TelegramVoiceArchiveDirection,
    TelegramVoiceArchiver,
    TelegramVoiceTranscriptStatus,
)
from app.application.telegram_voice_orchestration import (
    ProcessTelegramVoiceUpdateCommand,
    ProcessTelegramVoiceUpdateService,
    TelegramVoiceUpdateProcessor,
)
from app.application.telegram_voice_transcription import (
    TelegramVoiceTranscriptionService,
)
from app.application.telegram_voice_update import (
    extract_telegram_voice,
    project_transcribed_voice,
)
from app.application.telegram_work_ownership_shadow import (
    TelegramIngressRoutingView,
    evaluate_telegram_work_ownership_shadow,
)
from app.application.worker_health import TELEGRAM_WORKER, WorkerHealthReporter
from app.core.config import Settings, get_settings
from app.core.telegram_connectors import (
    TelegramConnectorResolutionError,
    TelegramConnectorRole,
)
from app.infrastructure.alpha_canary import SessionFactoryAlphaCanaryStore
from app.infrastructure.alpha_delivery import (
    SessionFactoryAlphaDeliveryStore,
    TelegramClientAlphaTextAdapter,
)
from app.infrastructure.alpha_shadow_worker import (
    SessionFactoryAlphaShadowTurnRepository,
)
from app.infrastructure.billing_budget import SqlBillingBudgetService
from app.infrastructure.canonical_messaging import SessionFactoryCanonicalIngressStore
from app.infrastructure.openai_agent_runtime import ConfiguredAlphaRuntimeFactory
from app.infrastructure.openai_transcription import (
    OpenAITranscriptionClient,
    OpenAITranscriptionConfig,
)
from app.infrastructure.telegram_business_text_inbox import (
    SessionFactoryTelegramBusinessTextInboxProjector,
)
from app.infrastructure.telegram_business_text_parity import (
    SessionFactoryTelegramBusinessTextParityObserver,
)
from app.infrastructure.telegram_ingress_identity import (
    SessionFactoryTelegramIngressIdentityReader,
)
from app.infrastructure.telegram_processing import SqlTelegramProcessingQueue
from app.infrastructure.universal_mayak_delivery import (
    TelegramBusinessUniversalMayakDelivery,
)
from app.infrastructure.universal_mayak_durable_store import (
    SessionFactoryUniversalMayakDemoStore,
)
from app.infrastructure.universal_mayak_model import (
    build_configured_universal_mayak_model,
)
from app.infrastructure.universal_mayak_private_runtime import (
    SessionFactoryApprovedPrivateBindingResolver,
)
from app.infrastructure.telegram_voice import (
    TelegramVoiceDownloadConfig,
    TelegramVoiceDownloader,
    TelegramVoiceNormalizer,
)
from app.infrastructure.telegram_voice_archive import (
    SessionFactoryTelegramVoiceArchiveStore,
)
from app.services.telegram import TelegramClient, build_telegram_http_client

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
    """Classify and optionally persist canonical facts without external effects."""

    def __init__(
        self,
        *,
        settings: Settings,
        identity_reader: TelegramIngressIdentityReader,
        runtime_logger: logging.Logger,
        canonical_store: CanonicalIngressStore | None = None,
        alpha_canary: ProcessAlphaCanaryUpdateService | None = None,
        voice_updates: TelegramVoiceUpdateProcessor | None = None,
        voice_archive: TelegramVoiceArchiver | None = None,
        business_text_parity: TelegramBusinessTextParityObserver | None = None,
        business_text_inbox: TelegramBusinessTextInboxProjector | None = None,
        universal_mayak: ProcessUniversalMayakPrivateMessageService | None = None,
    ) -> None:
        self._settings = settings
        self._connectors = settings.telegram_connectors
        self._identity_reader = identity_reader
        self._canonical_store = canonical_store
        self._alpha_canary = alpha_canary
        self._voice_updates = voice_updates
        self._voice_archive = voice_archive
        self._business_text_parity = business_text_parity
        self._business_text_inbox = business_text_inbox
        self._universal_mayak = universal_mayak
        self._logger = runtime_logger

    async def process(self, item: TelegramProcessingWorkItem) -> None:
        if item.payload.get("update_id") != item.update_id:
            raise TelegramUpdateProcessingError("worker_update_id_mismatch")
        try:
            connector = self._connectors.require_worker_connector(item.bot_key)
        except TelegramConnectorResolutionError as exc:
            raise TelegramUpdateProcessingError(exc.code) from None
        payload = item.payload
        voice_disposition = "not_applicable"
        if connector.role is TelegramConnectorRole.BUSINESS:
            try:
                voice = extract_telegram_voice(payload)
            except Exception:
                raise TelegramUpdateProcessingError(
                    "telegram_voice_update_invalid"
                ) from None
            if voice is not None:
                voice_identity_command = build_telegram_business_identity_command(payload)
                if voice_identity_command is None:
                    raise TelegramUpdateProcessingError(
                        "telegram_voice_identity_invalid"
                    )
                try:
                    voice_identity = await ResolveTelegramIngressIdentityService(
                        self._identity_reader
                    ).execute(voice_identity_command)
                except Exception:
                    raise TelegramUpdateProcessingError(
                        "telegram_voice_identity_failed"
                    ) from None
                if (
                    not voice_identity.resolved
                    or voice_identity.account_id is None
                    or voice_identity.project_id is None
                ):
                    raise TelegramUpdateProcessingError(
                        "telegram_voice_identity_unresolved"
                    )
                if not self._settings.gate_telegram_voice_archive_enabled:
                    raise TelegramUpdateProcessingError(
                        "telegram_voice_archive_disabled"
                    )
                if self._voice_archive is None:
                    raise TelegramUpdateProcessingError(
                        "telegram_voice_archive_not_configured"
                    )
                if voice_identity.direction not in {
                    TelegramIngressDirection.INBOUND,
                    TelegramIngressDirection.OUTBOUND,
                }:
                    raise TelegramUpdateProcessingError(
                        "telegram_voice_archive_direction_invalid"
                    )
                try:
                    archived = await self._voice_archive.archive(
                        ArchiveTelegramVoiceCommand(
                            receipt_id=item.receipt_id,
                            account_id=voice_identity.account_id,
                            project_id=voice_identity.project_id,
                            direction=TelegramVoiceArchiveDirection(
                                voice_identity.direction.value
                            ),
                            update=payload,
                            received_at=item.received_at,
                        )
                    )
                except Exception:
                    raise TelegramUpdateProcessingError(
                        "telegram_voice_archive_failed"
                    ) from None
                voice_disposition = (
                    "archive_duplicate" if archived.duplicate else "archived"
                )
                if (
                    archived.transcription_status
                    is TelegramVoiceTranscriptStatus.TRANSCRIBED
                    and archived.transcript_text is not None
                ):
                    payload = project_transcribed_voice(
                        payload,
                        envelope=voice,
                        transcript=archived.transcript_text,
                    )
                    voice_disposition = (
                        f"{voice_disposition}:persisted_transcript"
                    )
                elif archived.transcription_status is not None:
                    raise TelegramUpdateProcessingError(
                        archived.transcription_reason_code
                        or "telegram_voice_transcription_blocked"
                    )
                else:
                    if self._voice_updates is None:
                        raise TelegramUpdateProcessingError(
                            "telegram_voice_not_configured"
                        )
                    try:
                        voice_result = await self._voice_updates.execute(
                            ProcessTelegramVoiceUpdateCommand(
                                update=payload,
                                workspace_key=self._settings.gate_workspace_id,
                                connector_ref=connector.key.value,
                                period_key=item.received_at.strftime("%Y-%m"),
                                requested_microusd=(
                                    voice.voice.duration_seconds
                                    * self._settings.gate_telegram_voice_reserve_microusd_per_second
                                ),
                                occurred_at=item.received_at,
                                language=(
                                    self._settings.gate_telegram_voice_language
                                    or "ru"
                                ),
                                enabled=(
                                    self._settings.gate_telegram_voice_transcription_enabled
                                ),
                            )
                        )
                    except Exception:
                        raise TelegramUpdateProcessingError(
                            "telegram_voice_processing_failed"
                        ) from None
                    transcription = voice_result.transcription
                    if transcription is None:
                        raise TelegramUpdateProcessingError(
                            "telegram_voice_transcription_result_missing"
                        )
                    try:
                        await self._voice_archive.persist_transcript(
                            PersistTelegramVoiceTranscriptCommand(
                                archive_id=archived.archive_id,
                                status=TelegramVoiceTranscriptStatus(
                                    transcription.outcome.value
                                ),
                                transcribed_at=item.received_at,
                                text=transcription.alpha_input_text,
                                language=(
                                    self._settings.gate_telegram_voice_language
                                    or "ru"
                                ),
                                model=(
                                    self._settings.gate_telegram_voice_transcription_model
                                ),
                                reason_code=transcription.reason_code,
                            )
                        )
                    except Exception:
                        raise TelegramUpdateProcessingError(
                            "telegram_voice_transcript_persist_failed"
                        ) from None
                    voice_disposition = (
                        f"{voice_disposition}:{voice_result.disposition.value}"
                    )
                    if (
                        not voice_result.downstream_allowed
                        or voice_result.downstream_update is None
                    ):
                        raise TelegramUpdateProcessingError(
                            voice_result.reason_code
                        )
                    payload = voice_result.downstream_update

                if voice_identity.direction is TelegramIngressDirection.OUTBOUND:
                    self._logger.info(
                        "Telegram Business voice archived and transcribed: "
                        "direction=outbound duplicate=%s size_bytes=%s",
                        archived.duplicate,
                        archived.size_bytes,
                    )
                    return

        try:
            decision = classify_telegram_ingress(payload)
            routing = build_telegram_ingress_routing_context(
                payload,
                decision=decision,
                settings=self._settings,
            )
        except Exception as exc:
            raise TelegramUpdateProcessingError("shadow_classification_failed") from exc

        identity = not_applicable_identity_resolution()
        identity_reader_used = False
        direction_evidence = direction_evidence_from_telegram_message(payload)
        ownership_routing: TelegramIngressRoutingView = routing
        identity_command = (
            build_telegram_business_identity_command(payload)
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
                payload,
                decision=decision,
                routing=ownership_routing,
                connector_role=connector.role,
                bot_username=connector.bot_identity or "",
                direction_evidence=direction_evidence,
            )
        except Exception as exc:
            raise TelegramUpdateProcessingError("shadow_classification_failed") from exc

        canonical_persisted = False
        canonical_duplicate = False
        canonical_message_id: str | None = None
        projection = None
        if self._canonical_store is not None:
            try:
                projection = project_telegram_canonical_message(
                    payload,
                    workspace_id=self._settings.gate_workspace_id,
                    connector_key=connector.key.value,
                    connector_kind=_connector_kind(connector.role),
                    source_receipt_id=item.receipt_id,
                    received_at=item.received_at,
                    direction_evidence=direction_evidence,
                    reply_task_intent=identity.reply_task_intent.value,
                    ownership=ownership,
                    control_traffic=decision.command is not None,
                )
            except TelegramCanonicalProjectionError:
                raise TelegramUpdateProcessingError("canonical_projection_failed") from None
            if projection is not None:
                try:
                    persisted = await self._canonical_store.persist(
                        dialog=projection.dialog,
                        message=projection.message,
                        ownership=projection.ownership,
                    )
                except Exception:
                    raise TelegramUpdateProcessingError(
                        "canonical_ingress_persist_failed"
                    ) from None
                canonical_persisted = True
                canonical_duplicate = persisted.duplicate
                canonical_message_id = persisted.message_id

        business_text_inbox_disposition = "not_applicable"
        universal_mayak_disposition = "not_applicable"
        business_text_parity_disposition = "not_applicable"
        if (
            connector.role is TelegramConnectorRole.BUSINESS
            and voice_disposition == "not_applicable"
            and projection is not None
            and projection.message.text_present
        ):
            if (
                not identity.resolved
                or identity.account_id is None
                or identity.account_alias is None
                or identity_command is None
                or canonical_message_id is None
                or projection.message.text_content is None
            ):
                raise TelegramUpdateProcessingError(
                    "telegram_business_text_parity_identity_invalid"
                )
            if is_telegram_business_text_cutover_update(payload):
                if self._business_text_inbox is None:
                    raise TelegramUpdateProcessingError(
                        "telegram_business_text_inbox_not_configured"
                    )
                try:
                    inbox_command = build_telegram_business_text_inbox_command(
                        payload,
                        account_id=identity.account_id,
                        project_id=identity.project_id or "",
                        canonical_message_id=canonical_message_id,
                        bot_username=self._settings.telegram_bot_username,
                        update_id=item.update_id,
                        business_connection_id=(
                            identity_command.business_connection_id
                        ),
                        projection=projection,
                        route_override=(
                            WorkRoute.AGENT_WORK
                            if self._universal_mayak is not None
                            and self._settings.gate_universal_mayak_runtime_enabled
                            else None
                        ),
                    )
                    inbox_projection = await self._business_text_inbox.project(
                        inbox_command
                    )
                    mayak_processed = False
                    if (
                        self._universal_mayak is not None
                        and self._settings.gate_universal_mayak_runtime_enabled
                    ):
                        mayak_result = await self._universal_mayak.execute(
                            ProcessUniversalMayakPrivateMessageCommand(
                                account_id=identity.account_id,
                                project_id=identity.project_id or "",
                                canonical_message_id=canonical_message_id,
                                business_connection_id=(
                                    identity_command.business_connection_id
                                ),
                                provider_dialog_id=(
                                    projection.dialog.provider_dialog_id
                                ),
                                provider_actor_id=(
                                    projection.message.provider_sender_id or ""
                                ),
                                direction=projection.message.direction,
                                current_route=projection.ownership.route,
                                text=projection.message.text_content,
                                runtime_enabled=(
                                    self._settings.gate_universal_mayak_runtime_enabled
                                ),
                                connector_enabled=True,
                                credentials_ready=bool(
                                    self._settings.openai_api_key.get_secret_value()
                                    and self._settings.telegram_bot_token
                                ),
                                now=item.received_at,
                            )
                        )
                        universal_mayak_disposition = mayak_result.disposition.value
                        mayak_processed = (
                            mayak_result.disposition
                            is PrivateIngressDisposition.PROCESSED
                        )
                    if (
                        not mayak_processed
                        and inbox_command.route is WorkRoute.AGENT_WORK
                    ):
                        inbox_projection = await self._business_text_inbox.project(
                            build_telegram_business_text_inbox_command(
                                payload,
                                account_id=identity.account_id,
                                project_id=identity.project_id or "",
                                canonical_message_id=canonical_message_id,
                                bot_username=self._settings.telegram_bot_username,
                                update_id=item.update_id,
                                business_connection_id=(
                                    identity_command.business_connection_id
                                ),
                                projection=projection,
                            )
                        )
                except Exception:
                    raise TelegramUpdateProcessingError(
                        "telegram_business_text_inbox_projection_failed"
                    ) from None
                business_text_inbox_disposition = (
                    "inbox_duplicate"
                    if inbox_projection.duplicate
                    else "inbox_projected"
                )
            if self._business_text_parity is not None:
                try:
                    parity = await self._business_text_parity.observe(
                        TelegramBusinessTextParityCommand(
                            workspace_key=self._settings.gate_workspace_id,
                            account_id=identity.account_id,
                            canonical_message_id=canonical_message_id,
                            update_id=item.update_id,
                            account_alias=identity.account_alias,
                            direction=projection.message.direction,
                            route=projection.ownership.route,
                            provider_dialog_id=projection.dialog.provider_dialog_id,
                            business_connection_id=(
                                identity_command.business_connection_id
                            ),
                            text_content=projection.message.text_content,
                        )
                    )
                except TelegramBusinessTextParityError as exc:
                    raise TelegramUpdateProcessingError(exc.code) from None
                except Exception:
                    raise TelegramUpdateProcessingError(
                        "telegram_business_text_parity_failed"
                    ) from None
                business_text_parity_disposition = parity.disposition

        alpha_canary_disposition = "not_applicable"
        if connector.role is TelegramConnectorRole.ALPHA_GROUP:
            if self._settings.gate_alpha_canary_enabled and self._alpha_canary is None:
                raise TelegramUpdateProcessingError("alpha_canary_not_configured")
            if self._alpha_canary is not None:
                try:
                    canary = await self._alpha_canary.execute(
                        ProcessAlphaCanaryUpdateCommand(
                            workspace_key=self._settings.gate_workspace_id,
                            update=payload,
                            canonical_message_id=canonical_message_id,
                            bot_username=self._settings.telegram_alpha_bot_username,
                            connector_enabled=self._settings.gate_alpha_connector_enabled,
                            shadow_worker_enabled=self._settings.gate_alpha_shadow_worker_enabled,
                            canary_enabled=self._settings.gate_alpha_canary_enabled,
                            delivery_enabled=self._settings.gate_alpha_delivery_enabled,
                            privacy_mode_confirmed=self._settings.gate_alpha_privacy_mode_confirmed,
                            credentials_ready=bool(
                                self._settings.telegram_alpha_bot_token.get_secret_value()
                                and self._settings.telegram_alpha_webhook_secret.get_secret_value()
                            ),
                            now=datetime.now(UTC),
                        )
                    )
                except AlphaCanaryError as exc:
                    raise TelegramUpdateProcessingError(exc.code) from None
                except Exception:
                    raise TelegramUpdateProcessingError("alpha_canary_processing_failed") from None
                if (
                    canary.disposition is AlphaCanaryDisposition.BLOCKED
                    and canary.code == "rate_limited"
                ):
                    raise TelegramUpdateDeferred(
                        "alpha_rate_limited_deferred",
                        retry_after_seconds=61,
                    )
                alpha_canary_disposition = canary.disposition.value

        self._logger.info(
            "Telegram worker shadow classification: handler=%s reason=%s "
            "configured_account_resolution=%s identity_resolution=%s "
            "identity_reader_used=%s identity_direction=%s "
            "direction_evidence=%s reply_task_intent=%s "
            "ownership_proposed_state=%s ownership_candidate=%s "
            "ownership_reason=%s ownership_signal=%s ownership_requirements=%s "
            "ownership_policy=%s ownership_authoritative=%s "
            "canonical_persisted=%s canonical_duplicate=%s "
            "voice_disposition=%s business_text_inbox=%s "
            "universal_mayak=%s business_text_parity=%s "
            "alpha_canary_disposition=%s",
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
            canonical_persisted,
            canonical_duplicate,
            voice_disposition,
            business_text_inbox_disposition,
            universal_mayak_disposition,
            business_text_parity_disposition,
            alpha_canary_disposition,
        )


async def run_telegram_worker_once(
    *,
    settings: Settings,
    session: AsyncSession | None = None,
    queue: TelegramProcessingQueue | None = None,
    processor: TelegramUpdateProcessor | None = None,
    identity_reader: TelegramIngressIdentityReader | None = None,
    canonical_store: CanonicalIngressStore | None = None,
    alpha_canary: ProcessAlphaCanaryUpdateService | None = None,
    voice_updates: TelegramVoiceUpdateProcessor | None = None,
    voice_archive: TelegramVoiceArchiver | None = None,
    business_text_parity: TelegramBusinessTextParityObserver | None = None,
    business_text_inbox: TelegramBusinessTextInboxProjector | None = None,
    universal_mayak: ProcessUniversalMayakPrivateMessageService | None = None,
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
            raise RuntimeError("An enabled default Telegram worker requires an identity reader")
        processor = ShadowClassifyingTelegramProcessor(
            settings=settings,
            identity_reader=identity_reader,
            canonical_store=canonical_store,
            alpha_canary=alpha_canary,
            voice_updates=voice_updates,
            voice_archive=voice_archive,
            business_text_parity=business_text_parity,
            business_text_inbox=business_text_inbox,
            universal_mayak=universal_mayak,
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
    canonical_store = SessionFactoryCanonicalIngressStore(sessions)
    business_text_parity = SessionFactoryTelegramBusinessTextParityObserver(
        sessions
    )
    business_text_inbox = SessionFactoryTelegramBusinessTextInboxProjector(sessions)
    try:
        if (
            settings.gate_alpha_canary_enabled
            or settings.gate_telegram_voice_transcription_enabled
            or settings.gate_telegram_voice_archive_enabled
        ):
            async with build_telegram_http_client() as http_client:
                alpha_canary = None
                if settings.gate_alpha_canary_enabled:
                    telegram = TelegramClient(
                        credential=settings.telegram_alpha_bot_token.get_secret_value(),
                        api_base_url=os.getenv(
                            "TELEGRAM_API_BASE_URL", "https://api.telegram.org"
                        ),
                        http_client=http_client,
                    )
                    alpha_canary = ProcessAlphaCanaryUpdateService(
                        store=SessionFactoryAlphaCanaryStore(sessions),
                        shadow=ProcessAlphaShadowMessageService(
                            port=SessionFactoryAlphaShadowTurnRepository(
                                sessions,
                                verified_model_shadow_enabled=True,
                            ),
                            bundle_loader=DeployedInstructionBundleLoader(
                                Path(settings.gate_agent_bundle_root)
                            ),
                            runtime_factory=ConfiguredAlphaRuntimeFactory(
                                http_client=http_client,
                                api_key=settings.openai_api_key.get_secret_value(),
                                api_base_url=settings.openai_api_base_url,
                                model_runtime_enabled=settings.gate_alpha_model_runtime_enabled,
                                model_data_gate_approved=(
                                    settings.gate_alpha_model_data_gate_approved
                                ),
                                max_output_tokens=(
                                    settings.gate_alpha_model_max_output_tokens
                                ),
                                timeout_seconds=settings.gate_alpha_model_timeout_seconds,
                            ),
                        ),
                        delivery=DeliverAlphaGrantService(
                            store=SessionFactoryAlphaDeliveryStore(sessions),
                            telegram=TelegramClientAlphaTextAdapter(telegram),
                        ),
                    )
                voice_downloader = None
                if (
                    settings.gate_telegram_voice_archive_enabled
                    or settings.gate_telegram_voice_transcription_enabled
                ):
                    voice_downloader = TelegramVoiceDownloader(
                        config=TelegramVoiceDownloadConfig(
                            bot_token=settings.telegram_bot_token,
                            api_base_url=os.getenv(
                                "TELEGRAM_API_BASE_URL",
                                "https://api.telegram.org",
                            ),
                        ),
                        http=http_client,
                    )
                voice_archive = None
                if settings.gate_telegram_voice_archive_enabled:
                    if voice_downloader is None:
                        raise RuntimeError("Telegram voice downloader is required")
                    voice_archive = ArchiveTelegramVoiceService(
                        downloader=voice_downloader,
                        store=SessionFactoryTelegramVoiceArchiveStore(sessions),
                    )
                voice_updates = None
                if settings.gate_telegram_voice_transcription_enabled:
                    if voice_downloader is None:
                        raise RuntimeError("Telegram voice downloader is required")
                    api_key = settings.openai_api_key.get_secret_value()
                    voice_updates = ProcessTelegramVoiceUpdateService(
                        TelegramVoiceTranscriptionService(
                            downloader=voice_downloader,
                            normalizer=TelegramVoiceNormalizer(),
                            budget=SqlBillingBudgetService(sessions),
                            speech_to_text=OpenAITranscriptionClient(
                                config=OpenAITranscriptionConfig(
                                    api_key=api_key,
                                    enabled=bool(api_key),
                                    model=(
                                        settings.gate_telegram_voice_transcription_model
                                    ),
                                    base_url=settings.openai_api_base_url,
                                ),
                                http=http_client,
                            ),
                        )
                    )
                async with sessions() as session:
                    return await run_telegram_worker_once(
                        settings=settings,
                        session=session,
                        identity_reader=identity_reader,
                        canonical_store=canonical_store,
                        alpha_canary=alpha_canary,
                        business_text_parity=business_text_parity,
                        business_text_inbox=business_text_inbox,
                        voice_updates=voice_updates,
                        voice_archive=voice_archive,
                    )
        async with sessions() as session:
            return await run_telegram_worker_once(
                settings=settings,
                session=session,
                identity_reader=identity_reader,
                canonical_store=canonical_store,
                business_text_parity=business_text_parity,
                business_text_inbox=business_text_inbox,
            )
    finally:
        await engine.dispose()


async def run_telegram_worker_loop(
    settings: Settings,
    *,
    stop_event: asyncio.Event | None = None,
    run_iteration: Callable[
        [Settings], Awaitable[TelegramWorkerRuntimeResult]
    ] | None = None,
    idle_delay_seconds: float = 1.0,
    error_delay_seconds: float = 5.0,
    runtime_logger: logging.Logger = logger,
    health_reporter: WorkerHealthReporter | None = None,
) -> None:
    """Continuously drain due work while the explicit global worker gate is enabled."""

    if not settings.gate_telegram_worker_enabled:
        return
    if idle_delay_seconds <= 0 or error_delay_seconds <= 0:
        raise ValueError("worker loop delays must be positive")
    stop = stop_event or asyncio.Event()
    iteration = run_iteration or _run_from_settings
    runtime_logger.info("Telegram worker loop started")
    while not stop.is_set():
        try:
            result = await iteration(settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if health_reporter is not None:
                health_reporter.record_failure(
                    TELEGRAM_WORKER,
                    error_code="telegram_worker_iteration_failed",
                    observed_at=datetime.now(UTC),
                )
            runtime_logger.warning(
                "Telegram worker loop iteration failed: error_type=%s",
                exc.__class__.__name__,
            )
            delay = error_delay_seconds
        else:
            if health_reporter is not None and result.error_code is not None:
                health_reporter.record_failure(
                    TELEGRAM_WORKER,
                    error_code=result.error_code,
                    observed_at=datetime.now(UTC),
                )
            elif health_reporter is not None:
                health_reporter.record_success(
                    TELEGRAM_WORKER,
                    observed_at=datetime.now(UTC),
                )
            delay = idle_delay_seconds if result.disposition == "idle" else 0.0
        if stop.is_set():
            break
        if delay == 0:
            await asyncio.sleep(0)
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


def main() -> None:
    """Run one explicitly invoked iteration; never start a scheduler or loop."""

    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(_run_from_settings(get_settings()))
    logger.info(
        "Telegram worker iteration: enabled=%s disposition=%s attempt=%s error_code=%s",
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


def _connector_kind(role: TelegramConnectorRole) -> ConnectorKind:
    if role is TelegramConnectorRole.BUSINESS:
        return ConnectorKind.TELEGRAM_BUSINESS
    if role is TelegramConnectorRole.ALPHA_GROUP:
        return ConnectorKind.TELEGRAM_BOT
    raise TelegramCanonicalProjectionError("canonical_connector_role_invalid")
