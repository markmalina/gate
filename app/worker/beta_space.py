"""Autonomous bounded worker for post-admission Beta Space continuations."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from app.api.beta_telegram_ingress import (
    _BetaMaterialTelegram,
    _settle_beta_consent_callback,
    _UnavailableBetaWeddingModel,
)
from app.application.beta_material import BetaMaterialDisposition, ProcessBetaMaterial
from app.application.beta_space_intake import (
    BetaSpaceDisposition,
    DeliverBetaSpaceConsent,
    ProcessBetaSpaceUpdate,
)
from app.application.beta_space_work import (
    BetaSpaceWorkDisposition,
    BetaSpaceWorkFailurePlan,
    BetaSpaceWorkItem,
    BetaSpaceWorkQueue,
    BetaSpaceWorkRetryPolicy,
)
from app.application.beta_wedding_reply import (
    BetaWeddingReplyDisposition,
    BetaWeddingReplyModel,
    RespondToBetaWeddingMessage,
)
from app.core.config import Settings
from app.infrastructure.beta_candidate_binding import (
    BetaCandidateBindingError,
    extract_beta_callback_owner_candidate,
    prepare_beta_katya_candidate_binding,
)
from app.infrastructure.beta_checkpoint_dispatch import (
    GitHubAppBetaCheckpointDispatcher,
)
from app.infrastructure.beta_checkpoint_dispatch_drain import (
    SessionFactoryBetaCheckpointDispatchDrain,
)
from app.infrastructure.beta_material import SqlBetaMaterialStore
from app.infrastructure.beta_material_provider import (
    OpenAIBetaMaterialExtractor,
    OpenAIBetaMaterialStructurer,
    TelegramMaterialDownloadAdapter,
)
from app.infrastructure.beta_space import (
    BETA_CONNECTOR_KEY,
    SqlBetaSpaceStore,
    beta_space_production_ready,
)
from app.infrastructure.beta_space_work import SessionFactoryBetaSpaceWorkQueue
from app.infrastructure.beta_wedding_reply import (
    BetaWeddingModelError,
    OpenAIResponsesBetaWeddingModel,
    SqlBetaWeddingReplyStore,
)
from app.models.beta_space import (
    BetaConsentDeliveryModel,
    BetaSpaceWorkItemModel,
)
from app.models.domain import TelegramReceipt
from app.services.telegram import TelegramClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class BetaSpacePermanentWorkError(RuntimeError):
    """Terminal safe failure that must not be retried."""

    def __init__(self, code: str) -> None:
        self.code = code[:128]
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class BetaSpaceWorkerResult:
    disposition: str
    kind: str | None = None
    attempt: int | None = None
    error_code: str | None = None


class BetaSpaceWorkProcessorPort(Protocol):
    async def process(self, item: BetaSpaceWorkItem, *, now: datetime) -> str: ...


class BetaSpaceWorkProcessor:
    def __init__(
        self,
        *,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        telegram: TelegramClient,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._telegram = telegram
        self._http_client = http_client

    async def process(self, item: BetaSpaceWorkItem, *, now: datetime) -> str:
        if item.kind == "candidate_consent":
            return await self._candidate_consent(item, now=now)
        if item.kind == "callback_delivery":
            return await self._callback_delivery(item)
        if item.kind == "consent_delivery":
            return await self._consent_delivery(item, now=now)
        if item.kind == "reply":
            return await self._reply(item, now=now)
        if item.kind == "material":
            return await self._material(item, now=now)
        raise BetaSpacePermanentWorkError("beta_space_work_kind_invalid")

    async def _candidate_consent(
        self, item: BetaSpaceWorkItem, *, now: datetime
    ) -> str:
        update, received_at = await self._receipt(item.receipt_id)
        owner = extract_beta_callback_owner_candidate(update)
        if owner is None:
            raise BetaSpacePermanentWorkError("beta_callback_owner_candidate_invalid")
        member_response = await self._telegram.get_chat_member(
            chat_id=owner.telegram_chat_id,
            user_id=owner.telegram_user_id,
        )
        member = member_response.get("result")
        actor = member.get("user") if isinstance(member, dict) else None
        actor_id = actor.get("id") if isinstance(actor, dict) else None
        if (
            not isinstance(member, dict)
            or member.get("status") != "creator"
            or not isinstance(actor, dict)
            or actor.get("is_bot") is not False
            or type(actor_id) is not int
            or str(actor_id) != owner.telegram_user_id
        ):
            raise BetaSpacePermanentWorkError("beta_callback_owner_not_exact")
        async with self._sessions() as session:
            try:
                await prepare_beta_katya_candidate_binding(
                    session,
                    workspace_key=self._settings.gate_workspace_id,
                    now=received_at,
                    telegram_chat_id=owner.telegram_chat_id,
                    telegram_user_id=owner.telegram_user_id,
                )
                store = SqlBetaSpaceStore(session)
                intake = await ProcessBetaSpaceUpdate(
                    store=store,
                    production_ready=beta_space_production_ready(self._settings),
                    workspace_key=self._settings.gate_workspace_id,
                    connector_key=BETA_CONNECTOR_KEY,
                ).execute(
                    update=update,
                    receipt_id=item.receipt_id,
                    received_at=received_at,
                )
                if intake.disposition is BetaSpaceDisposition.CONSENT_DECLINED:
                    await store.enqueue_callback_delivery(
                        receipt_id=item.receipt_id,
                        now=received_at,
                    )
            except BetaCandidateBindingError as exc:
                raise BetaSpacePermanentWorkError(exc.code) from exc
        if intake.disposition not in {
            BetaSpaceDisposition.CONSENT_ACCEPTED,
            BetaSpaceDisposition.CONSENT_DECLINED,
            BetaSpaceDisposition.DUPLICATE,
        }:
            raise BetaSpacePermanentWorkError(
                f"beta_candidate_intake_{intake.disposition.value}"
            )
        return intake.disposition.value

    async def _callback_delivery(self, item: BetaSpaceWorkItem) -> str:
        update, _ = await self._receipt(item.receipt_id)
        callback = update.get("callback_query")
        data = callback.get("data") if isinstance(callback, dict) else None
        disposition = (
            BetaSpaceDisposition.CONSENT_ACCEPTED
            if data == "beta:katya:consent:yes"
            else BetaSpaceDisposition.CONSENT_DECLINED
        )
        result = await _settle_beta_consent_callback(
            telegram=self._telegram,
            update=update,
            disposition=disposition,
        )
        if result != "answered":
            raise BetaSpacePermanentWorkError("beta_callback_delivery_unknown")
        return result

    async def _consent_delivery(
        self, item: BetaSpaceWorkItem, *, now: datetime
    ) -> str:
        async with self._sessions() as session:
            event_id = await session.scalar(
                select(BetaSpaceWorkItemModel.event_id).where(
                    BetaSpaceWorkItemModel.id == uuid.UUID(item.work_id)
                )
            )
            delivery_id = await session.scalar(
                select(BetaConsentDeliveryModel.id).where(
                    BetaConsentDeliveryModel.event_id == event_id
                )
            )
            if delivery_id is None:
                raise BetaSpacePermanentWorkError(
                    "beta_consent_delivery_work_invalid"
                )
            result = await DeliverBetaSpaceConsent(
                store=SqlBetaSpaceStore(session),
                telegram=self._telegram,
                enabled=beta_space_production_ready(self._settings),
            ).execute(delivery_id=str(delivery_id), now=now)
        if result == "unknown":
            raise BetaSpacePermanentWorkError("beta_consent_delivery_unknown")
        return result

    async def _reply(self, item: BetaSpaceWorkItem, *, now: datetime) -> str:
        try:
            model: BetaWeddingReplyModel = OpenAIResponsesBetaWeddingModel(
                http_client=self._http_client,
                api_key=self._settings.openai_api_key.get_secret_value(),
                api_base_url=self._settings.openai_api_base_url,
                model=self._settings.gate_beta_model_profile,
                max_output_tokens=self._settings.gate_beta_model_max_output_tokens,
                timeout_seconds=self._settings.gate_beta_model_timeout_seconds,
            )
        except BetaWeddingModelError:
            model = _UnavailableBetaWeddingModel()
        async with self._sessions() as session:
            result = await RespondToBetaWeddingMessage(
                store=SqlBetaWeddingReplyStore(session),
                model=model,
                telegram=self._telegram,
            ).execute(receipt_id=item.receipt_id, now=now)
        if result.disposition is BetaWeddingReplyDisposition.UNKNOWN:
            raise BetaSpacePermanentWorkError(result.result_code)
        if result.disposition is BetaWeddingReplyDisposition.NOT_APPLICABLE:
            raise BetaSpacePermanentWorkError(result.result_code)
        return result.result_code

    async def _material(self, item: BetaSpaceWorkItem, *, now: datetime) -> str:
        downloader = TelegramMaterialDownloadAdapter(
            credential=self._settings.telegram_beta_bot_token.get_secret_value(),
            api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
            client=self._http_client,
        )
        async with self._sessions() as session:
            result = await ProcessBetaMaterial(
                store=SqlBetaMaterialStore(session),
                telegram=_BetaMaterialTelegram(self._telegram, downloader),
                extractor=OpenAIBetaMaterialExtractor(
                    api_key=self._settings.openai_api_key.get_secret_value(),
                    api_base_url=self._settings.openai_api_base_url,
                    client=self._http_client,
                    vision_model=self._settings.gate_beta_model_profile,
                ),
                structurer=OpenAIBetaMaterialStructurer(
                    api_key=self._settings.openai_api_key.get_secret_value(),
                    api_base_url=self._settings.openai_api_base_url,
                    model=self._settings.gate_beta_model_profile,
                    client=self._http_client,
                ),
            ).execute(receipt_id=item.receipt_id, now=now)
        if result.disposition in {
            BetaMaterialDisposition.UNKNOWN,
            BetaMaterialDisposition.NOT_APPLICABLE,
        }:
            raise BetaSpacePermanentWorkError(result.code)
        return result.code

    async def _receipt(self, receipt_id: str) -> tuple[dict[str, object], datetime]:
        async with self._sessions() as session:
            receipt = await session.get(TelegramReceipt, uuid.UUID(receipt_id))
            if receipt is None or not isinstance(receipt.payload, dict):
                raise BetaSpacePermanentWorkError("beta_space_receipt_missing")
            return receipt.payload, receipt.received_at


async def run_beta_space_worker_once(
    *,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    processor: BetaSpaceWorkProcessorPort,
    queue: BetaSpaceWorkQueue | None = None,
    now: datetime | None = None,
    lease_seconds: int = 120,
) -> BetaSpaceWorkerResult:
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    work_queue = queue or SessionFactoryBetaSpaceWorkQueue(sessions)
    item = await work_queue.claim_due(
        now=observed_at,
        lease_seconds=lease_seconds,
    )
    if item is None:
        return BetaSpaceWorkerResult("idle")
    if item.kind == "callback_delivery":
        await work_queue.mark_effect_started(item=item, now=observed_at)
    try:
        await processor.process(item, now=observed_at)
    except asyncio.CancelledError:
        raise
    except BetaSpacePermanentWorkError as exc:
        if exc.code.endswith("_unknown"):
            await work_queue.mark_unknown(
                item=item,
                now=observed_at,
                error_code=exc.code,
            )
            return BetaSpaceWorkerResult(
                "unknown", item.kind, item.attempt, exc.code
            )
        await work_queue.fail(
            item=item,
            now=observed_at,
            plan=BetaSpaceWorkFailurePlan(
                BetaSpaceWorkDisposition.FAILED,
                None,
                exc.code,
            ),
        )
        return BetaSpaceWorkerResult("failed", item.kind, item.attempt, exc.code)
    except Exception as exc:
        error_code = f"beta_space_{item.kind}_{exc.__class__.__name__.lower()}"
        plan = BetaSpaceWorkRetryPolicy().plan(
            item=item,
            failed_at=observed_at,
            error_code=error_code,
        )
        await work_queue.fail(item=item, now=observed_at, plan=plan)
        return BetaSpaceWorkerResult(
            plan.disposition.value,
            item.kind,
            item.attempt,
            plan.error_code,
        )
    await work_queue.complete(item=item, now=observed_at)
    return BetaSpaceWorkerResult("completed", item.kind, item.attempt)


async def run_beta_space_worker_loop(
    *,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    http_client: httpx.AsyncClient,
    stop_event: asyncio.Event | None = None,
    idle_delay_seconds: float = 1.0,
) -> None:
    """Continuously drain one bounded continuation at a time."""

    if not settings.gate_beta_connector_enabled:
        return
    if idle_delay_seconds <= 0:
        raise ValueError("idle_delay_seconds must be positive")
    stop = stop_event or asyncio.Event()
    processor = BetaSpaceWorkProcessor(
        settings=settings,
        sessions=sessions,
        telegram=telegram,
        http_client=http_client,
    )
    dispatch_drain = None
    if settings.gate_beta_checkpoint_dispatch_enabled:
        app_id = settings.gate_beta_checkpoint_github_app_id
        installation_id = settings.gate_beta_checkpoint_github_installation_id
        private_key = (
            settings.gate_beta_checkpoint_github_private_key.get_secret_value()
        )
        if app_id and installation_id and private_key:
            dispatch_drain = SessionFactoryBetaCheckpointDispatchDrain(
                sessions,
                GitHubAppBetaCheckpointDispatcher(
                    client=http_client,
                    app_id=app_id,
                    installation_id=installation_id,
                    private_key_pem=private_key,
                ),
            )
        else:
            logger.warning(
                "Beta checkpoint dispatch worker disabled: configuration incomplete"
            )
    while not stop.is_set():
        try:
            result = await run_beta_space_worker_once(
                settings=settings,
                sessions=sessions,
                processor=processor,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Beta Space worker iteration failed: error_type=%s",
                exc.__class__.__name__,
            )
            delay = 5.0
        else:
            dispatch_disposition = "idle"
            if dispatch_drain is not None:
                dispatch_result = await dispatch_drain.drain_once()
                dispatch_disposition = dispatch_result.disposition
            delay = (
                idle_delay_seconds
                if result.disposition == "idle" and dispatch_disposition == "idle"
                else 0.0
            )
        if delay == 0:
            await asyncio.sleep(0)
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
