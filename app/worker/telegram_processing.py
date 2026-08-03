"""Disabled-by-default runtime for durable Telegram outbox processing."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.alpha_canary import (
    BETA_DISCLOSURE_PREFIX,
    AlphaCanaryDisposition,
    AlphaCanaryError,
    ProcessAlphaCanaryUpdateCommand,
    ProcessAlphaCanaryUpdateService,
)
from app.application.alpha_delivery import DeliverAlphaGrantService
from app.application.alpha_shadow_worker import ProcessAlphaShadowMessageService
from app.application.become_controlled_consent import (
    ProcessBecomeConsentCommand,
    ProcessBecomeConsentService,
    is_consent_command,
)
from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
    ProcessBecomeRegistrationCommand,
    ProcessBecomeRegistrationService,
)
from app.application.become_media_intake import (
    ProcessBecomeMediaCommand,
    ProcessBecomeMediaService,
    is_become_media_update,
)
from app.application.become_media_prompt import (
    DeliverBecomeMediaPromptService,
)
from app.application.become_transcript_review import (
    DeliverPilotTranscriptReview,
    PilotReviewDisposition,
    ProcessPilotTranscriptReviewUpdate,
)
from app.application.become_voice_transcription import (
    ProcessBecomeVoiceTranscriptionService,
)
from app.application.canonical_messaging import (
    CanonicalIngressStore,
    ConnectorKind,
    WorkRoute,
)
from app.application.instruction_bundles import DeployedInstructionBundleLoader
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
from app.application.telegram_queue_maintenance import (
    RecoverStaleTelegramClaimsCommand,
    RecoverStaleTelegramClaimsService,
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
from app.application.universal_mayak_demo import ProcessUniversalMayakDemoService
from app.application.universal_mayak_private_ingress import (
    PrivateIngressDisposition,
    ProcessUniversalMayakPrivateMessageCommand,
    ProcessUniversalMayakPrivateMessageService,
)
from app.application.worker_health import TELEGRAM_WORKER, WorkerHealthReporter
from app.core.config import Settings, get_settings
from app.core.telegram_connectors import (
    TelegramConnectorKey,
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
from app.infrastructure.become_controlled_consent import (
    SessionFactoryBecomeControlledConsentStore,
    SessionFactoryBecomeRelationsHandoffBackfiller,
    SessionFactoryBecomeRelationsHandoffDeliverer,
)
from app.infrastructure.become_controlled_intake import (
    SessionFactoryBecomeControlledIntakeStore,
    SessionFactoryBecomeRegistrationReplayer,
    TelegramBecomeOwnerPresence,
)
from app.infrastructure.become_i4_execution import (
    SessionFactoryBecomeI4Executor,
)
from app.infrastructure.become_i4_recovery_execution import (
    SessionFactoryBecomeI4RecoveryExecutor,
)
from app.infrastructure.become_media_intake import (
    SessionFactoryBecomeMediaRetention,
    SessionFactoryBecomeMediaStore,
    TelegramBecomeMediaDownloader,
)
from app.infrastructure.become_media_prompt import (
    SessionFactoryBecomeMediaPromptStore,
    TelegramClientBecomeMediaPromptAdapter,
)
from app.infrastructure.become_transcript_review import (
    SessionFactoryPilotTranscriptReviewStore,
)
from app.infrastructure.become_voice_chain import (
    SessionFactoryBecomeVoiceChainStore,
)
from app.infrastructure.become_voice_transcription import (
    SessionFactoryBecomeVoiceTranscriptionStore,
)
from app.infrastructure.beta_context import SessionFactoryBetaContextTransfer
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
from app.infrastructure.telegram_processing import (
    SqlAlphaCapacityDeadLetterReplayer,
    SqlTelegramProcessingQueue,
)
from app.infrastructure.telegram_queue_maintenance import (
    SqlTelegramQueueMaintenance,
)
from app.infrastructure.telegram_voice import (
    TelegramVoiceDownloadConfig,
    TelegramVoiceDownloader,
    TelegramVoiceNormalizer,
)
from app.infrastructure.telegram_voice_archive import (
    SessionFactoryTelegramVoiceArchiveStore,
)
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
        beta_canary: ProcessAlphaCanaryUpdateService | None = None,
        become_canary: ProcessAlphaCanaryUpdateService | None = None,
        become_intake: ProcessBecomeRegistrationService | None = None,
        become_consent: ProcessBecomeConsentService | None = None,
        become_media: ProcessBecomeMediaService | None = None,
        become_review: ProcessPilotTranscriptReviewUpdate | None = None,
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
        self._beta_canary = beta_canary
        self._become_canary = become_canary
        self._become_intake = become_intake
        self._become_consent = become_consent
        self._become_media = become_media
        self._become_review = become_review
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
        if (
            connector.key is TelegramConnectorKey.BECOME_GROUP
            and self._become_review is not None
        ):
            try:
                review_result = await self._become_review.execute(
                    update=payload,
                    now=item.received_at,
                )
            except BecomeControlledIntakeError as exc:
                raise TelegramUpdateProcessingError(exc.code) from None
            if (
                review_result.disposition
                is not PilotReviewDisposition.NOT_APPLICABLE
            ):
                await self._become_review.tombstone_receipt(
                    receipt_id=item.receipt_id,
                    now=item.received_at,
                )
                self._logger.info(
                    "BECOME Pilot review update persisted: disposition=%s",
                    review_result.disposition.value,
                )
                return
        if (
            connector.key is TelegramConnectorKey.BECOME_GROUP
            and is_consent_command(
                payload,
                invocation_command="become",
                bot_username="BecomeNowBot",
            )
        ):
            if self._become_consent is None:
                raise TelegramUpdateProcessingError(
                    "become_consent_intake_not_configured"
                )
            try:
                result = await self._become_consent.execute(
                    ProcessBecomeConsentCommand(
                        workspace_key=self._settings.gate_workspace_id,
                        source_receipt_id=item.receipt_id,
                        update=payload,
                        received_at=item.received_at,
                    )
                )
            except BecomeControlledIntakeError as exc:
                raise TelegramUpdateProcessingError(exc.code) from None
            except Exception:
                raise TelegramUpdateProcessingError(
                    "become_consent_intake_failed"
                ) from None
            self._logger.info(
                "BECOME controlled consent persisted: duplicate=%s",
                result.duplicate,
            )
            return
        if (
            connector.key is TelegramConnectorKey.BECOME_GROUP
            and is_become_media_update(payload)
        ):
            if self._become_media is None:
                raise TelegramUpdateProcessingError(
                    "become_media_intake_not_configured"
                )
            try:
                media_result = await self._become_media.execute(
                    ProcessBecomeMediaCommand(
                        workspace_key=self._settings.gate_workspace_id,
                        source_receipt_id=item.receipt_id,
                        update=payload,
                        received_at=item.received_at,
                    )
                )
            except BecomeControlledIntakeError as exc:
                raise TelegramUpdateProcessingError(exc.code) from None
            except Exception:
                raise TelegramUpdateProcessingError(
                    "become_media_intake_failed"
                ) from None
            self._logger.info(
                (
                    "BECOME controlled media archived: "
                    "kind=%s duplicate=%s"
                ),
                media_result.kind.value,
                media_result.duplicate,
            )
            if self._become_review is not None:
                await self._become_review.tombstone_receipt(
                    receipt_id=item.receipt_id,
                    now=item.received_at,
                )
            return
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
                            WorkRoute.AGENT_TURN
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
                        and inbox_command.route is WorkRoute.AGENT_TURN
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
            if connector.key is TelegramConnectorKey.BETA_GROUP:
                group_canary = self._beta_canary
                canary_enabled = self._settings.gate_beta_canary_enabled
                bot_username = self._settings.telegram_beta_bot_username
                connector_enabled = self._settings.gate_beta_connector_enabled
                shadow_worker_enabled = self._settings.gate_beta_shadow_worker_enabled
                delivery_enabled = self._settings.gate_beta_delivery_enabled
                privacy_mode_confirmed = self._settings.gate_beta_privacy_mode_confirmed
                credentials_ready = bool(
                    self._settings.telegram_beta_bot_token.get_secret_value()
                    and self._settings.telegram_beta_webhook_secret.get_secret_value()
                )
                invocation_command = "beta"
                allow_untargeted_command = True
            elif connector.key is TelegramConnectorKey.BECOME_GROUP:
                group_canary = self._become_canary
                canary_enabled = self._settings.gate_become_canary_enabled
                bot_username = self._settings.telegram_become_bot_username
                connector_enabled = self._settings.gate_become_connector_enabled
                shadow_worker_enabled = self._settings.gate_become_shadow_worker_enabled
                delivery_enabled = self._settings.gate_become_delivery_enabled
                privacy_mode_confirmed = (
                    self._settings.gate_become_privacy_mode_confirmed
                )
                credentials_ready = bool(
                    self._settings.telegram_become_bot_token.get_secret_value()
                    and self._settings.telegram_become_webhook_secret.get_secret_value()
                )
                invocation_command = "become"
                allow_untargeted_command = False
            else:
                group_canary = self._alpha_canary
                canary_enabled = self._settings.gate_alpha_canary_enabled
                bot_username = self._settings.telegram_alpha_bot_username
                connector_enabled = self._settings.gate_alpha_connector_enabled
                shadow_worker_enabled = self._settings.gate_alpha_shadow_worker_enabled
                delivery_enabled = self._settings.gate_alpha_delivery_enabled
                privacy_mode_confirmed = self._settings.gate_alpha_privacy_mode_confirmed
                credentials_ready = bool(
                    self._settings.telegram_alpha_bot_token.get_secret_value()
                    and self._settings.telegram_alpha_webhook_secret.get_secret_value()
                )
                invocation_command = "alpha"
                allow_untargeted_command = False
            if canary_enabled and group_canary is None:
                raise TelegramUpdateProcessingError("alpha_canary_not_configured")
            if group_canary is not None:
                try:
                    canary = await group_canary.execute(
                        ProcessAlphaCanaryUpdateCommand(
                            workspace_key=self._settings.gate_workspace_id,
                            update=payload,
                            canonical_message_id=canonical_message_id,
                            bot_username=bot_username,
                            connector_enabled=connector_enabled,
                            shadow_worker_enabled=shadow_worker_enabled,
                            canary_enabled=canary_enabled,
                            delivery_enabled=delivery_enabled,
                            privacy_mode_confirmed=privacy_mode_confirmed,
                            credentials_ready=credentials_ready,
                            invocation_command=invocation_command,
                            allow_untargeted_command=allow_untargeted_command,
                            allow_registration_command=(
                                connector.key is TelegramConnectorKey.BECOME_GROUP
                            ),
                            now=datetime.now(UTC),
                        )
                    )
                except AlphaCanaryError as exc:
                    raise TelegramUpdateProcessingError(exc.code) from None
                except Exception:
                    raise TelegramUpdateProcessingError(
                        "alpha_canary_processing_failed"
                    ) from None
                if (
                    connector.key is TelegramConnectorKey.BECOME_GROUP
                    and canary.code == "alpha_registration_command_present"
                ):
                    if self._become_intake is None:
                        raise TelegramUpdateProcessingError(
                            "become_registration_intake_not_configured"
                        )
                    membership = canary.membership
                    if membership is None or membership.installation_key is None:
                        raise TelegramUpdateProcessingError(
                            "become_registration_installation_missing"
                        )
                    try:
                        await self._become_intake.execute(
                            ProcessBecomeRegistrationCommand(
                                workspace_key=self._settings.gate_workspace_id,
                                source_receipt_id=item.receipt_id,
                                installation_ref=membership.installation_key,
                                update=payload,
                                received_at=item.received_at,
                            )
                        )
                    except BecomeControlledIntakeError as exc:
                        raise TelegramUpdateProcessingError(exc.code) from None
                    except Exception:
                        raise TelegramUpdateProcessingError(
                            "become_registration_intake_failed"
                        ) from None
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
    beta_canary: ProcessAlphaCanaryUpdateService | None = None,
    become_canary: ProcessAlphaCanaryUpdateService | None = None,
    become_intake: ProcessBecomeRegistrationService | None = None,
    become_consent: ProcessBecomeConsentService | None = None,
    become_media: ProcessBecomeMediaService | None = None,
    become_review: ProcessPilotTranscriptReviewUpdate | None = None,
    voice_updates: TelegramVoiceUpdateProcessor | None = None,
    voice_archive: TelegramVoiceArchiver | None = None,
    business_text_parity: TelegramBusinessTextParityObserver | None = None,
    business_text_inbox: TelegramBusinessTextInboxProjector | None = None,
    universal_mayak: ProcessUniversalMayakPrivateMessageService | None = None,
    mayak_delivery_recovery: TelegramBusinessUniversalMayakDelivery | None = None,
    stale_recovery: RecoverStaleTelegramClaimsService | None = None,
    now: datetime | None = None,
    runtime_logger: logging.Logger = logger,
) -> TelegramWorkerRuntimeResult:
    """Run at most one item, failing closed before database access when disabled."""

    if not settings.gate_telegram_worker_enabled:
        return TelegramWorkerRuntimeResult(enabled=False, disposition="disabled")
    iteration_at = now or datetime.now(UTC)
    if stale_recovery is not None:
        recovery = await stale_recovery.execute(
            RecoverStaleTelegramClaimsCommand(
                stale_before=iteration_at - timedelta(minutes=5),
                recovered_at=iteration_at,
                max_attempts=5,
                limit=1,
                require_persisted_voice_transcript=True,
            )
        )
        if recovery.recovered:
            runtime_logger.info(
                "Recovered stale persisted-transcript voice claim: "
                "recovered=%s retry_scheduled=%s dead_lettered=%s",
                recovery.recovered,
                recovery.retry_scheduled,
                recovery.dead_lettered,
            )
        else:
            recovery = await stale_recovery.execute(
                RecoverStaleTelegramClaimsCommand(
                    stale_before=iteration_at - timedelta(minutes=5),
                    recovered_at=iteration_at,
                    max_attempts=5,
                    limit=1,
                    require_missing_voice_archive=True,
                )
            )
            if recovery.recovered:
                runtime_logger.info(
                    "Recovered stale missing-archive voice claim: "
                    "recovered=%s retry_scheduled=%s dead_lettered=%s",
                    recovery.recovered,
                    recovery.retry_scheduled,
                    recovery.dead_lettered,
                )
    if mayak_delivery_recovery is not None:
        receipt = await mayak_delivery_recovery.recover_one_binding_block()
        if receipt is not None:
            runtime_logger.info(
                "Recovered exact owner Mayak delivery: state=%s",
                receipt.state.value,
            )
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
            beta_canary=beta_canary,
            become_canary=become_canary,
            become_intake=become_intake,
            become_consent=become_consent,
            become_media=become_media,
            become_review=become_review,
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
    ).run_once(now=iteration_at)
    return TelegramWorkerRuntimeResult(
        enabled=True,
        disposition=result.disposition.value,
        attempt=result.attempt,
        error_code=result.error_code,
    )


def _optional_installation_key(raw_value: str) -> str | None:
    """Treat a blank optional pin as discovery while normalizing a real token."""

    normalized = raw_value.strip()
    return normalized or None


async def _run_from_settings(settings: Settings) -> TelegramWorkerRuntimeResult:
    if not settings.gate_telegram_worker_enabled:
        return await run_telegram_worker_once(settings=settings)
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for an enabled Telegram worker")

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    if (
        settings.gate_become_connector_enabled
        and settings.gate_become_canary_enabled
        and settings.gate_become_privacy_mode_confirmed
        and settings.gate_become_group_capacity == 1
    ):
        recovered_registration = await SessionFactoryBecomeRegistrationReplayer(
            sessions
        ).requeue_latest(now=datetime.now(UTC))
        if recovered_registration:
            logger.info(
                "Requeued one durable BECOME registration for controlled intake"
            )
        projected_consent = await SessionFactoryBecomeRelationsHandoffBackfiller(
            sessions
        ).ensure_one()
        if projected_consent:
            logger.info(
                "Projected one durable BECOME consent for Relations handoff"
            )
        retention = await SessionFactoryBecomeMediaRetention(
            sessions
        ).purge_expired(now=datetime.now(UTC))
        if retention.raw_purged or retention.transcripts_purged:
            logger.info(
                (
                    "Purged expired BECOME content: "
                    "raw=%s transcripts=%s"
                ),
                retention.raw_purged,
                retention.transcripts_purged,
            )
    if settings.gate_alpha_membership_replay_limit:
        if (
            not settings.gate_alpha_connector_enabled
            or not settings.gate_alpha_canary_enabled
            or settings.gate_alpha_group_capacity < 2
        ):
            raise RuntimeError(
                "Alpha membership replay requires the enabled canary and group capacity above one"
            )
        async with sessions() as replay_session:
            replayed = await SqlAlphaCapacityDeadLetterReplayer(
                replay_session
            ).requeue(
                bot_key=TelegramConnectorKey.ALPHA_GROUP.value,
                now=datetime.now(UTC),
                limit=settings.gate_alpha_membership_replay_limit,
            )
        if replayed:
            logger.info(
                "Requeued bounded Alpha membership capacity failures: count=%s",
                replayed,
            )
    identity_reader = SessionFactoryTelegramIngressIdentityReader(sessions)
    canonical_store = SessionFactoryCanonicalIngressStore(sessions)
    business_text_parity = SessionFactoryTelegramBusinessTextParityObserver(
        sessions
    )
    business_text_inbox = SessionFactoryTelegramBusinessTextInboxProjector(sessions)
    try:
        if (
            settings.gate_alpha_canary_enabled
            or settings.gate_beta_canary_enabled
            or settings.gate_become_canary_enabled
            or settings.gate_universal_mayak_runtime_enabled
            or settings.gate_telegram_voice_transcription_enabled
            or settings.gate_telegram_voice_archive_enabled
        ):
            async with build_telegram_http_client() as http_client:
                if (
                    settings.gate_become_connector_enabled
                    and settings.gate_become_canary_enabled
                    and settings.gate_become_privacy_mode_confirmed
                    and settings.gate_become_group_capacity == 1
                ):
                    delivered_consent = (
                        await SessionFactoryBecomeRelationsHandoffDeliverer(
                            sessions,
                            http_client=http_client,
                        ).deliver_one()
                    )
                    if delivered_consent:
                        logger.info(
                            "Delivered one BECOME consent handoff to Relations"
                        )
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
                        store=SessionFactoryAlphaCanaryStore(
                            sessions,
                            group_capacity=settings.gate_alpha_group_capacity,
                        ),
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
                beta_canary = None
                if settings.gate_beta_canary_enabled:
                    beta_telegram = TelegramClient(
                        credential=settings.telegram_beta_bot_token.get_secret_value(),
                        api_base_url=os.getenv(
                            "TELEGRAM_API_BASE_URL", "https://api.telegram.org"
                        ),
                        http_client=http_client,
                    )
                    beta_canary = ProcessAlphaCanaryUpdateService(
                        store=SessionFactoryAlphaCanaryStore(
                            sessions,
                            group_capacity=settings.gate_beta_group_capacity,
                            pinned_installation_key=_optional_installation_key(
                                settings.gate_beta_installation_key
                            ),
                        ),
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
                            telegram=TelegramClientAlphaTextAdapter(beta_telegram),
                        ),
                        connector_key=TelegramConnectorKey.BETA_GROUP.value,
                        disclosure_prefix=BETA_DISCLOSURE_PREFIX,
                    )
                become_canary = None
                become_review = None
                if settings.gate_become_canary_enabled:
                    become_telegram = TelegramClient(
                        credential=settings.telegram_become_bot_token.get_secret_value(),
                        api_base_url=os.getenv(
                            "TELEGRAM_API_BASE_URL", "https://api.telegram.org"
                        ),
                        http_client=http_client,
                    )
                    become_canary = ProcessAlphaCanaryUpdateService(
                        store=SessionFactoryAlphaCanaryStore(
                            sessions,
                            group_capacity=settings.gate_become_group_capacity,
                        ),
                        shadow=ProcessAlphaShadowMessageService(
                            port=SessionFactoryAlphaShadowTurnRepository(
                                sessions,
                                verified_model_shadow_enabled=False,
                            ),
                            bundle_loader=DeployedInstructionBundleLoader(
                                Path(settings.gate_agent_bundle_root)
                            ),
                            runtime_factory=ConfiguredAlphaRuntimeFactory(
                                http_client=http_client,
                                api_key="",
                                api_base_url=settings.openai_api_base_url,
                                model_runtime_enabled=False,
                                model_data_gate_approved=False,
                                max_output_tokens=1,
                                timeout_seconds=settings.gate_alpha_model_timeout_seconds,
                            ),
                        ),
                        delivery=DeliverAlphaGrantService(
                            store=SessionFactoryAlphaDeliveryStore(sessions),
                            telegram=TelegramClientAlphaTextAdapter(become_telegram),
                        ),
                        connector_key=TelegramConnectorKey.BECOME_GROUP.value,
                    )
                    become_presence = TelegramBecomeOwnerPresence(
                        bot_token=(
                            settings.telegram_become_bot_token.get_secret_value()
                        ),
                        http_client=http_client,
                    )
                    subject_pepper = (
                        settings.telegram_become_webhook_secret.get_secret_value()
                    )
                    become_intake = ProcessBecomeRegistrationService(
                        store=SessionFactoryBecomeControlledIntakeStore(sessions),
                        presence=become_presence,
                        subject_pepper=subject_pepper,
                    )
                    become_consent = ProcessBecomeConsentService(
                        store=SessionFactoryBecomeControlledConsentStore(sessions),
                        presence=become_presence,
                        subject_pepper=subject_pepper,
                    )
                    review_store = SessionFactoryPilotTranscriptReviewStore(
                        sessions
                    )
                    become_review = ProcessPilotTranscriptReviewUpdate(
                        store=review_store,
                        telegram=become_telegram,
                        identity_pepper=subject_pepper,
                        enabled=settings.gate_become_pilot_slice1_enabled,
                    )
                    become_media = ProcessBecomeMediaService(
                        store=SessionFactoryBecomeMediaStore(sessions),
                        downloader=TelegramBecomeMediaDownloader(
                            bot_token=(
                                settings.telegram_become_bot_token
                                .get_secret_value()
                            ),
                            http_client=http_client,
                            api_base_url=os.getenv(
                                "TELEGRAM_API_BASE_URL",
                                "https://api.telegram.org",
                            ),
                        ),
                        presence=become_presence,
                        subject_pepper=subject_pepper,
                    )
                    try:
                        correlation = await (
                            SessionFactoryBecomeVoiceChainStore(
                                sessions
                            ).reconcile_one(
                                workspace_key=settings.gate_workspace_id,
                                prepared_at=datetime.now(UTC),
                            )
                        )
                    except BecomeControlledIntakeError as exc:
                        logger.warning(
                            "BECOME voice correlation blocked: code=%s",
                            exc.code,
                        )
                    except Exception as exc:
                        logger.warning(
                            (
                                "BECOME voice correlation deferred: "
                                "error_type=%s"
                            ),
                            exc.__class__.__name__,
                        )
                    else:
                        if correlation is not None:
                            logger.info(
                                (
                                    "BECOME voice correlation ready: "
                                    "duplicate=%s slots=%s"
                                ),
                                correlation.duplicate,
                                correlation.slot_count,
                            )
                    try:
                        api_key = settings.openai_api_key.get_secret_value()
                        transcription_result = await (
                            ProcessBecomeVoiceTranscriptionService(
                                store=(
                                    SessionFactoryBecomeVoiceTranscriptionStore(
                                        sessions
                                    )
                                ),
                                normalizer=TelegramVoiceNormalizer(),
                                budget=SqlBillingBudgetService(sessions),
                                speech_to_text=OpenAITranscriptionClient(
                                    config=OpenAITranscriptionConfig(
                                        api_key=api_key,
                                        enabled=(
                                            settings
                                            .gate_become_voice_transcription_enabled
                                            and settings
                                            .gate_become_voice_transcription_data_gate_approved
                                            and settings
                                            .gate_become_voice_transcription_max_calls
                                            == 1
                                            and bool(api_key)
                                        ),
                                        model=(
                                            settings
                                            .gate_become_voice_transcription_model
                                        ),
                                        base_url=settings.openai_api_base_url,
                                        timeout_seconds=(
                                            settings
                                            .gate_become_voice_transcription_timeout_seconds
                                        ),
                                    ),
                                    http=http_client,
                                ),
                                workspace_key=settings.gate_workspace_id,
                                model=(
                                    settings
                                    .gate_become_voice_transcription_model
                                ),
                                language=(
                                    settings
                                    .gate_become_voice_transcription_language
                                ),
                                reserve_microusd_per_second=(
                                    settings
                                    .gate_become_voice_transcription_reserve_microusd_per_second
                                ),
                                enabled=(
                                    settings
                                    .gate_become_voice_transcription_enabled
                                ),
                                data_gate_approved=(
                                    settings
                                    .gate_become_voice_transcription_data_gate_approved
                                ),
                                max_calls=(
                                    settings
                                    .gate_become_voice_transcription_max_calls
                                ),
                            ).run_once(now=datetime.now(UTC))
                        )
                    except BecomeControlledIntakeError as exc:
                        logger.warning(
                            "BECOME voice transcription blocked: code=%s",
                            exc.code,
                        )
                    except Exception as exc:
                        logger.warning(
                            (
                                "BECOME voice transcription deferred: "
                                "error_type=%s"
                            ),
                            exc.__class__.__name__,
                        )
                    else:
                        if transcription_result.disposition != "disabled":
                            logger.info(
                                (
                                    "BECOME voice transcription state: "
                                    "disposition=%s reconciliation=%s"
                                ),
                                transcription_result.disposition,
                                transcription_result.reconciliation_required,
                            )
                    try:
                        chain_result = await SessionFactoryBecomeI4Executor(
                            sessions,
                            http_client=http_client,
                            enabled=(
                                settings.gate_become_i4_chain_execution_enabled
                            ),
                        ).run_once(
                            workspace_key=settings.gate_workspace_id,
                            now=datetime.now(UTC),
                        )
                    except BecomeControlledIntakeError as exc:
                        logger.warning(
                            "BECOME I4 chain blocked: code=%s",
                            exc.code,
                        )
                    except Exception as exc:
                        logger.warning(
                            "BECOME I4 chain deferred: error_type=%s",
                            exc.__class__.__name__,
                        )
                    else:
                        if chain_result.disposition != "disabled":
                            logger.info(
                                (
                                    "BECOME I4 chain state: disposition=%s "
                                    "stage=%s attempted=%s reconciliation=%s"
                                ),
                                chain_result.disposition,
                                (
                                    chain_result.stage.value
                                    if chain_result.stage is not None
                                    else "none"
                                ),
                                chain_result.attempted,
                                chain_result.reconciliation_required,
                            )
                    try:
                        recovery_result = (
                            await SessionFactoryBecomeI4RecoveryExecutor(
                                sessions,
                                http_client=http_client,
                                enabled=(
                                    settings
                                    .gate_become_i4_recovery_execution_enabled
                                ),
                                max_posts=(
                                    settings.gate_become_i4_recovery_max_posts
                                ),
                            ).run_once(
                                workspace_key=settings.gate_workspace_id,
                                now=datetime.now(UTC),
                            )
                        )
                    except Exception as exc:
                        logger.warning(
                            "BECOME I4 recovery deferred: error_type=%s",
                            exc.__class__.__name__,
                        )
                    else:
                        if recovery_result.disposition != "disabled":
                            logger.info(
                                (
                                    "BECOME I4 recovery state: "
                                    "disposition=%s stage=%s attempted=%s "
                                    "reconciliation=%s"
                                ),
                                recovery_result.disposition,
                                (
                                    recovery_result.stage.value
                                    if recovery_result.stage is not None
                                    else "none"
                                ),
                                recovery_result.attempted,
                                recovery_result.reconciliation_required,
                            )
                    try:
                        review_delivery = await DeliverPilotTranscriptReview(
                            store=review_store,
                            telegram=become_telegram,
                            enabled=settings.gate_become_pilot_slice1_enabled,
                            max_operations=(
                                settings.gate_become_pilot_slice1_max_operations
                            ),
                        ).run_once(now=datetime.now(UTC))
                    except BecomeControlledIntakeError as exc:
                        logger.warning(
                            "BECOME Pilot delivery blocked: code=%s",
                            exc.code,
                        )
                    except Exception as exc:
                        logger.warning(
                            "BECOME Pilot delivery deferred: error_type=%s",
                            exc.__class__.__name__,
                        )
                    else:
                        if review_delivery != "disabled":
                            logger.info(
                                "BECOME Pilot delivery state: disposition=%s",
                                review_delivery,
                            )
                    try:
                        prompt_result = await (
                            DeliverBecomeMediaPromptService(
                                store=SessionFactoryBecomeMediaPromptStore(
                                    sessions
                                ),
                                presence=become_presence,
                                telegram=(
                                    TelegramClientBecomeMediaPromptAdapter(
                                        become_telegram
                                    )
                                ),
                            ).execute(
                                workspace_key=settings.gate_workspace_id,
                                now=datetime.now(UTC),
                            )
                        )
                    except BecomeControlledIntakeError as exc:
                        logger.warning(
                            "BECOME media prompt blocked: code=%s",
                            exc.code,
                        )
                    except Exception as exc:
                        logger.warning(
                            (
                                "BECOME media prompt deferred: "
                                "error_type=%s"
                            ),
                            exc.__class__.__name__,
                        )
                    else:
                        if prompt_result.sent:
                            logger.info(
                                (
                                    "BECOME media reply prompt ready: "
                                    "duplicate=%s"
                                ),
                                prompt_result.duplicate,
                            )
                else:
                    become_intake = None
                    become_consent = None
                    become_media = None
                universal_mayak = None
                universal_mayak_delivery = None
                if settings.gate_universal_mayak_runtime_enabled:
                    bundle = DeployedInstructionBundleLoader(
                        Path(settings.gate_agent_bundle_root)
                    ).load("universal_mayak_oko_v2")
                    model = build_configured_universal_mayak_model(
                        bundle=bundle,
                        http_client=http_client,
                        api_key=settings.openai_api_key.get_secret_value(),
                        api_base_url=settings.openai_api_base_url,
                        model_profile=(
                            settings.gate_universal_mayak_model_profile
                        ),
                        runtime_enabled=(
                            settings.gate_universal_mayak_runtime_enabled
                        ),
                        data_gate_approved=(
                            settings.gate_universal_mayak_model_data_gate_approved
                        ),
                        max_output_tokens=(
                            settings.gate_universal_mayak_max_output_tokens
                        ),
                        timeout_seconds=(
                            settings.gate_universal_mayak_timeout_seconds
                        ),
                    )
                    business_telegram = TelegramClient(
                        credential=settings.telegram_bot_token,
                        api_base_url=os.getenv(
                            "TELEGRAM_API_BASE_URL", "https://api.telegram.org"
                        ),
                        http_client=http_client,
                    )
                    universal_mayak_delivery = (
                        TelegramBusinessUniversalMayakDelivery(
                            sessions=sessions,
                            telegram=business_telegram,
                            enabled=settings.gate_universal_mayak_delivery_enabled,
                        )
                    )
                    universal_mayak = ProcessUniversalMayakPrivateMessageService(
                        bindings=SessionFactoryApprovedPrivateBindingResolver(
                            sessions
                        ),
                        mayak=ProcessUniversalMayakDemoService(
                            store=SessionFactoryUniversalMayakDemoStore(
                                sessions
                            ),
                            model=model,
                            delivery=universal_mayak_delivery,

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
                    result = await run_telegram_worker_once(
                        settings=settings,
                        session=session,
                        identity_reader=identity_reader,
                        canonical_store=canonical_store,
                        alpha_canary=alpha_canary,
                        beta_canary=beta_canary,
                        become_canary=become_canary,
                        become_intake=become_intake,
                        become_consent=become_consent,
                        become_media=become_media,
                        become_review=become_review,
                        business_text_parity=business_text_parity,
                        business_text_inbox=business_text_inbox,
                        universal_mayak=universal_mayak,
                        mayak_delivery_recovery=universal_mayak_delivery,
                        voice_updates=voice_updates,
                        voice_archive=voice_archive,
                        stale_recovery=(
                            RecoverStaleTelegramClaimsService(
                                SqlTelegramQueueMaintenance(session)
                            )
                            if (
                                settings.gate_telegram_voice_archive_enabled
                                and settings.gate_telegram_voice_transcription_enabled
                            )
                            else None
                        ),
                    )
                await _drain_beta_context(sessions)
                return result
        async with sessions() as session:
            result = await run_telegram_worker_once(
                settings=settings,
                session=session,
                identity_reader=identity_reader,
                canonical_store=canonical_store,
                business_text_parity=business_text_parity,
                business_text_inbox=business_text_inbox,
            )
        await _drain_beta_context(sessions)
        return result
    finally:
        await engine.dispose()


async def _drain_beta_context(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Retry pending Beta events without blocking Telegram delivery."""

    try:
        accepted = await SessionFactoryBetaContextTransfer(sessions).drain_once()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Beta context transfer deferred: error_type=%s",
            exc.__class__.__name__,
        )
        return
    if accepted:
        logger.info("Beta context transfer accepted: count=%s", accepted)


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
