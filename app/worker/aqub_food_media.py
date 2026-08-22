"""Durable asynchronous worker for owner-only AQUB Food media."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.aqub_telegram import _project_inbound
from app.application.aqub_food_media import AQUBFoodMediaError, ProcessAQUBFoodMedia
from app.application.aqub_live import (
    HttpAQUBRuntimeClient,
    forward_aqub_media_result,
)
from app.application.aqub_transport import AQUBTransportInbound
from app.core.config import Settings
from app.infrastructure.aqub_food_media import (
    OpenAIAQUBFoodMediaAnalyzer,
    SqlAQUBFoodMediaStore,
    TelegramAQUBFoodMediaDownloader,
)
from app.infrastructure.aqub_installation import load_aqub_production_binding
from app.models.aqub import AQUBFoodMedia
from app.models.domain import TelegramReceipt
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)

AQUB_FOOD_MEDIA_PROGRESS_SECONDS = 21
_AQUB_FOOD_MEDIA_POLL_SECONDS = 1
_STALE_PROCESSING_SECONDS = 120
_ACK_TEXT = "Спасибо, файл принят. Разбираюсь с ним."
_PROGRESS_TEXT = "Нужно больше времени, продолжаю разбираться."


@dataclass(frozen=True, slots=True, repr=False)
class _MediaJob:
    receipt_id: UUID
    status: str
    error_code: str | None
    result_json: dict[str, Any] | None
    progress_sent: bool
    payload: dict[str, Any]
    received_at: datetime


async def run_aqub_food_media_once(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    http_client: httpx.AsyncClient,
    now: datetime | None = None,
) -> int:
    """Process and settle at most one durable media job."""

    observed_at = now or datetime.now(UTC)
    job = await _discover_job(session_factory, now=observed_at)
    if job is None:
        return 0

    async with session_factory() as session:
        binding = await load_aqub_production_binding(session, settings)
        await session.rollback()

    inbound = _project_inbound(
        job.payload,
        bot_username=binding.bot_username,
        received_at=job.received_at,
    )
    if inbound is None:
        await _send_final_failure(
            session_factory=session_factory,
            telegram=telegram,
            inbound=None,
            fallback_chat_id=binding.group_chat_id,
            receipt_id=job.receipt_id,
            code="aqub_food_media_inbound_invalid",
        )
        return 1

    if job.status == "failed":
        await _send_final_failure(
            session_factory=session_factory,
            telegram=telegram,
            inbound=inbound,
            fallback_chat_id=binding.group_chat_id,
            receipt_id=job.receipt_id,
            code=job.error_code or "aqub_food_media_processing_failed",
        )
        return 1

    try:
        if job.status == "processed":
            if not isinstance(job.result_json, dict):
                raise AQUBFoodMediaError("aqub_food_media_result_missing")
            media_result = job.result_json
        else:
            task = asyncio.create_task(
                _process_job(
                    settings=settings,
                    session_factory=session_factory,
                    http_client=http_client,
                    job=job,
                    inbound=inbound,
                    now=observed_at,
                )
            )
            done, _pending = await asyncio.wait(
                {task},
                timeout=_progress_timeout_seconds(
                    received_at=job.received_at,
                    observed_at=observed_at,
                ),
            )
            if not done and not job.progress_sent:
                await telegram.send_plain_message(
                    chat_id=inbound.chat_id,
                    text=_PROGRESS_TEXT,
                )
                async with session_factory() as session:
                    await SqlAQUBFoodMediaStore(session).mark_progress_sent(
                        receipt_id=str(job.receipt_id),
                        now=datetime.now(UTC),
                    )
            media_result = await task

        runtime_url = os.getenv("GATE_AQUB_RUNTIME_URL", "").strip()
        if not runtime_url:
            raise AQUBFoodMediaError("aqub_runtime_unavailable")
        downstream = await forward_aqub_media_result(
            binding=binding,
            inbound=inbound,
            command_receipt_id=str(job.receipt_id),
            media_result=media_result,
            runtime=HttpAQUBRuntimeClient(
                base_url=runtime_url,
                http_client=http_client,
            ),
        )
        reply_text = _reply_text(downstream)
        await telegram.send_plain_message(chat_id=inbound.chat_id, text=reply_text)
        await _mark_final(
            session_factory=session_factory,
            receipt_id=job.receipt_id,
        )
        return 1
    except AQUBFoodMediaError as exc:
        code = _safe_error_code(exc)
    except Exception:
        logger.exception("AQUB Food media worker job failed safely")
        code = "aqub_food_media_processing_failed"

    if code == "aqub_food_media_in_progress":
        return 0
    await _send_final_failure(
        session_factory=session_factory,
        telegram=telegram,
        inbound=inbound,
        fallback_chat_id=binding.group_chat_id,
        receipt_id=job.receipt_id,
        code=code,
    )
    return 1


async def _process_job(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
    job: _MediaJob,
    inbound: AQUBTransportInbound,
    now: datetime,
) -> dict[str, Any]:
    async with session_factory() as session:
        return await ProcessAQUBFoodMedia(
            store=SqlAQUBFoodMediaStore(session),
            downloader=TelegramAQUBFoodMediaDownloader(
                bot_token=settings.telegram_aqub_bot_token.get_secret_value(),
                client=http_client,
            ),
            analyzer=OpenAIAQUBFoodMediaAnalyzer(
                api_key=settings.openai_api_key.get_secret_value(),
                api_base_url=settings.openai_api_base_url,
                model=settings.gate_aqub_food_media_model,
                transcription_model=settings.gate_aqub_food_media_transcription_model,
                client=http_client,
                data_gate_approved=settings.gate_aqub_food_media_data_gate_approved,
            ),
        ).process(
            receipt_id=str(job.receipt_id),
            inbound=inbound,
            now=now,
        )


async def _discover_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> _MediaJob | None:
    stale_before = now - timedelta(seconds=_STALE_PROCESSING_SECONDS)
    async with session_factory() as session:
        result = await session.execute(
            select(
                AQUBFoodMedia.receipt_id,
                AQUBFoodMedia.status,
                AQUBFoodMedia.error_code,
                AQUBFoodMedia.result_json,
                AQUBFoodMedia.progress_sent_at,
                TelegramReceipt.payload,
                TelegramReceipt.received_at,
            )
            .join(TelegramReceipt, TelegramReceipt.id == AQUBFoodMedia.receipt_id)
            .where(
                AQUBFoodMedia.final_sent_at.is_(None),
                or_(
                    AQUBFoodMedia.status.in_(("queued", "processed", "failed")),
                    and_(
                        AQUBFoodMedia.status == "processing",
                        AQUBFoodMedia.processing_started_at <= stale_before,
                    ),
                ),
            )
            .order_by(AQUBFoodMedia.created_at, AQUBFoodMedia.id)
            .limit(1)
        )
        row = result.mappings().first()
        await session.rollback()
    if row is None:
        return None
    payload = row["payload"]
    if not isinstance(payload, dict):
        return None
    result_json = row["result_json"]
    return _MediaJob(
        receipt_id=row["receipt_id"],
        status=str(row["status"]),
        error_code=row["error_code"],
        result_json=dict(result_json) if isinstance(result_json, dict) else None,
        progress_sent=row["progress_sent_at"] is not None,
        payload=payload,
        received_at=row["received_at"],
    )


async def _send_final_failure(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    inbound: AQUBTransportInbound | None,
    fallback_chat_id: str,
    receipt_id: UUID,
    code: str,
) -> None:
    safe_code = _safe_error_code(AQUBFoodMediaError(code))
    await telegram.send_plain_message(
        chat_id=inbound.chat_id if inbound is not None else fallback_chat_id,
        text=(
            "Файл сохранил, но сейчас не смог разобрать. "
            "Пришлите его ещё раз или опишите еду текстом. "
            f"Код диагностики: {safe_code}."
        ),
    )
    await _mark_final(session_factory=session_factory, receipt_id=receipt_id)


async def _mark_final(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    receipt_id: UUID,
) -> None:
    async with session_factory() as session:
        await SqlAQUBFoodMediaStore(session).mark_final_sent(
            receipt_id=str(receipt_id),
            now=datetime.now(UTC),
        )


def _progress_timeout_seconds(
    *,
    received_at: datetime,
    observed_at: datetime,
) -> float:
    deadline = received_at + timedelta(seconds=AQUB_FOOD_MEDIA_PROGRESS_SECONDS)
    return max(0.0, (deadline - observed_at).total_seconds())


def _reply_text(downstream: dict[str, Any]) -> str:
    domain = downstream.get("result")
    if not isinstance(domain, dict):
        raise AQUBFoodMediaError("aqub_food_media_downstream_invalid")
    value = domain.get("reply_text")
    if not isinstance(value, str) or not value.strip() or len(value) > 4_096:
        raise AQUBFoodMediaError("aqub_food_media_downstream_invalid")
    return value.strip()


def _safe_error_code(error: Exception) -> str:
    value = str(error)
    if (
        value
        and len(value) <= 128
        and all(character.isalnum() or character in "_.-" for character in value)
    ):
        return value
    return "aqub_food_media_processing_failed"


async def run_aqub_food_media_worker_loop(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    http_client: httpx.AsyncClient,
) -> None:
    """Poll durable Food media jobs while the existing media gate is enabled."""

    while True:
        try:
            settled = await run_aqub_food_media_once(
                settings=settings,
                session_factory=session_factory,
                telegram=telegram,
                http_client=http_client,
            )
            if settled:
                logger.info("AQUB Food media worker settled one job")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AQUB Food media worker iteration failed safely")
        await asyncio.sleep(_AQUB_FOOD_MEDIA_POLL_SECONDS)
