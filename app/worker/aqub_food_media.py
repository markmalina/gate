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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.aqub_telegram import _project_inbound
from app.application.aqub_food_media import (
    AQUBFoodMediaAnalysis,
    AQUBFoodMediaError,
    ProcessAQUBFoodMedia,
    combine_album_media_results,
    validate_food_media_result,
)
from app.application.aqub_live import (
    HttpAQUBRuntimeClient,
    forward_aqub_album_result,
    forward_aqub_media_result,
)
from app.application.aqub_transport import AQUBTransportInbound
from app.core.config import Settings
from app.infrastructure.aqub_food_media import (
    OpenAIAQUBFoodMediaAnalyzer,
    SqlAQUBFoodMediaStore,
    TelegramAQUBFoodMediaDownloader,
)
from app.infrastructure.aqub_food_route import select_aqub_food_inbound_binding
from app.infrastructure.aqub_installation import load_aqub_production_binding
from app.models.aqub import AQUBFoodMedia
from app.models.domain import TelegramReceipt
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)

AQUB_FOOD_MEDIA_PROGRESS_SECONDS = 21
_AQUB_FOOD_MEDIA_POLL_SECONDS = 1
_STALE_PROCESSING_SECONDS = 120
_ALBUM_SETTLE_SECONDS = 2
_TELEGRAM_ORDER_MAX = 9_223_372_036_854_775_807
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
    processing_started_at: datetime | None = None
    reply_cursor: int = 0


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
    discovered = await _discover_job(session_factory, now=observed_at)
    jobs = discovered if isinstance(discovered, list) else [discovered] if discovered else []
    if not jobs:
        return 0

    async with session_factory() as session:
        fallback_binding = await load_aqub_production_binding(session, settings)
        await session.rollback()

    inbounds = [
        _project_inbound(
            job.payload,
            bot_username=fallback_binding.bot_username,
            received_at=job.received_at,
        )
        for job in jobs
    ]
    if any(inbound is None for inbound in inbounds):
        await _send_batch_failure(
            session_factory=session_factory,
            telegram=telegram,
            fallback_chat_id=fallback_binding.group_chat_id,
            receipt_ids=[job.receipt_id for job in jobs],
            code="aqub_food_media_inbound_invalid",
        )
        return 1
    projected = [inbound for inbound in inbounds if inbound is not None]
    async with session_factory() as session:
        binding = await select_aqub_food_inbound_binding(
            session,
            settings,
            fallback_binding=fallback_binding,
            chat_id=projected[0].chat_id,
        )
        await session.rollback()
    if len(jobs) > 1:
        return await _run_album(
            settings=settings,
            session_factory=session_factory,
            telegram=telegram,
            http_client=http_client,
            binding=binding,
            jobs=jobs,
            inbounds=projected,
            observed_at=observed_at,
        )
    job = jobs[0]
    inbound = projected[0]

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
            media_result = await _process_job(
                settings=settings,
                session_factory=session_factory,
                http_client=http_client,
                job=job,
                inbound=inbound,
                now=observed_at,
            )

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
        await _deliver_reply_messages(
            session_factory=session_factory,
            telegram=telegram,
            chat_id=inbound.chat_id,
            messages=_reply_messages(downstream),
            receipt_ids=[job.receipt_id],
            cursor=job.reply_cursor,
        )
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


async def _run_album(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    http_client: httpx.AsyncClient,
    binding: Any,
    jobs: list[_MediaJob],
    inbounds: list[AQUBTransportInbound],
    observed_at: datetime,
) -> int:
    tasks: list[asyncio.Task[dict[str, Any]]] = []
    task_orders: list[int] = []
    results: dict[int, dict[str, Any]] = {}
    for order, (job, inbound) in enumerate(zip(jobs, inbounds, strict=True)):
        if job.status == "processed" and isinstance(job.result_json, dict):
            results[order] = job.result_json
        elif job.status != "failed":
            task_orders.append(order)
            tasks.append(
                asyncio.create_task(
                    _process_job(
                        settings=settings,
                        session_factory=session_factory,
                        http_client=http_client,
                        job=job,
                        inbound=inbound,
                        now=observed_at,
                    )
                )
            )
    if tasks:
        gather = asyncio.gather(*tasks, return_exceptions=True)
        completed = await gather
        for order, value in zip(task_orders, completed, strict=True):
            if isinstance(value, dict):
                results[order] = value
    if not results:
        await _send_batch_failure(
            session_factory=session_factory,
            telegram=telegram,
            fallback_chat_id=inbounds[0].chat_id,
            receipt_ids=[job.receipt_id for job in jobs],
            code="aqub_food_album_processing_failed",
        )
        return 1
    try:
        ordered_results = [results[index] for index in sorted(results)]
        aggregate = combine_album_media_results(ordered_results)
        source_values = {str(value.get("source_sha256")) for value in ordered_results}
        if len(ordered_results) > 1 and len(source_values) > 1:
            try:
                analyzer = OpenAIAQUBFoodMediaAnalyzer(
                    api_key=settings.openai_api_key.get_secret_value(),
                    api_base_url=settings.openai_api_base_url,
                    model=settings.gate_aqub_food_media_model,
                    transcription_model=settings.gate_aqub_food_media_transcription_model,
                    client=http_client,
                    data_gate_approved=settings.gate_aqub_food_media_data_gate_approved,
                )
                captions = [_caption(inbounds[index]) for index in sorted(results)]
                reconciled = await analyzer.reconcile_album(
                    analyses=ordered_results,
                    captions=captions,
                )
                aggregate = validate_food_media_result(
                    reconciled.result_json,
                    source_kind="photo",
                    source_sha256=str(aggregate["source_sha256"]),
                )
                durable = AQUBFoodMediaAnalysis(
                    result_json=aggregate,
                    provider_model=reconciled.provider_model,
                    provider_request_ref=reconciled.provider_request_ref,
                )
                async with session_factory() as session:
                    saved = await SqlAQUBFoodMediaStore(session).save_album_result(
                        receipt_ids=[str(jobs[index].receipt_id) for index in sorted(results)],
                        analysis=durable,
                        now=datetime.now(UTC),
                    )
                if saved != len(results):
                    raise AQUBFoodMediaError("aqub_food_album_persistence_conflict")
            except AQUBFoodMediaError as exc:
                logger.warning(
                    "AQUB Food album reconciliation used deterministic fallback: code=%s",
                    _safe_error_code(exc),
                )
        async with session_factory() as session:
            linked_rows = await SqlAQUBFoodMediaStore(session).linked_texts_for_album(
                inbounds=inbounds,
                now=datetime.now(UTC),
            )
        linked_inbounds = [
            _project_inbound(
                payload,
                bot_username=binding.bot_username,
                received_at=received_at,
            )
            for _receipt_id, payload, received_at in linked_rows
        ]
        if any(item is None for item in linked_inbounds):
            raise AQUBFoodMediaError("aqub_food_album_linked_text_invalid")
        projected_linked = [item for item in linked_inbounds if item is not None]
        runtime_url = os.getenv("GATE_AQUB_RUNTIME_URL", "").strip()
        if not runtime_url:
            raise AQUBFoodMediaError("aqub_runtime_unavailable")
        downstream = await forward_aqub_album_result(
            binding=binding,
            inbounds=inbounds,
            command_receipt_ids=[str(job.receipt_id) for job in jobs],
            successful_orders=sorted(results),
            media_result=aggregate,
            runtime=HttpAQUBRuntimeClient(base_url=runtime_url, http_client=http_client),
            linked_text_inbounds=projected_linked,
            linked_text_receipt_ids=[receipt_id for receipt_id, _payload, _at in linked_rows],
        )
        await _deliver_reply_messages(
            session_factory=session_factory,
            telegram=telegram,
            chat_id=inbounds[0].chat_id,
            messages=_reply_messages(downstream),
            receipt_ids=[jobs[index].receipt_id for index in sorted(results)],
            cursor=max((job.reply_cursor for job in jobs), default=0),
        )
        media_receipt_ids = [str(job.receipt_id) for job in jobs]
        linked_receipt_ids = [receipt_id for receipt_id, _payload, _received_at in linked_rows]
        async with session_factory() as session:
            finalized = await SqlAQUBFoodMediaStore(session).finalize_album(
                media_receipt_ids=media_receipt_ids,
                linked_text_receipt_ids=linked_receipt_ids,
                now=datetime.now(UTC),
            )
        if finalized != (len(media_receipt_ids), len(linked_receipt_ids)):
            raise AQUBFoodMediaError("aqub_food_album_finalize_conflict")
        return 1
    except Exception:
        logger.exception("AQUB Food album worker failed safely")
        return 0


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
) -> list[_MediaJob]:
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
                AQUBFoodMedia.processing_started_at,
                AQUBFoodMedia.error_detail,
            )
            .join(TelegramReceipt, TelegramReceipt.id == AQUBFoodMedia.receipt_id)
            .where(
                AQUBFoodMedia.final_sent_at.is_(None),
            )
            .order_by(AQUBFoodMedia.created_at, AQUBFoodMedia.id)
            .limit(500)
        )
        rows = list(result.mappings())
        await session.rollback()
    jobs: list[_MediaJob] = []
    for row in rows:
        payload = row["payload"]
        if not isinstance(payload, dict):
            continue
        result_json = row["result_json"]
        jobs.append(
            _MediaJob(
                receipt_id=row["receipt_id"],
                status=str(row["status"]),
                error_code=row["error_code"],
                result_json=dict(result_json) if isinstance(result_json, dict) else None,
                progress_sent=row["progress_sent_at"] is not None,
                payload=payload,
                received_at=row["received_at"],
                processing_started_at=row["processing_started_at"],
                reply_cursor=_parse_reply_cursor(row["error_detail"]),
            )
        )
    return _select_ready_jobs(jobs, now=now, stale_before=stale_before)


def _select_ready_jobs(
    jobs: list[_MediaJob],
    *,
    now: datetime,
    stale_before: datetime,
) -> list[_MediaJob]:
    """Pick one isolated single item or one settled durable album."""

    def eligible(job: _MediaJob) -> bool:
        return job.status in {"queued", "processed", "failed"} or (
            job.status == "processing"
            and job.processing_started_at is not None
            and job.processing_started_at <= stale_before
        )

    for candidate in jobs:
        key = _album_key(candidate.payload)
        if key is None:
            if eligible(candidate):
                return [candidate]
            continue
        group = [job for job in jobs if _album_key(job.payload) == key]
        if not all(eligible(job) for job in group):
            continue
        if max(job.received_at for job in group) > now - timedelta(seconds=_ALBUM_SETTLE_SECONDS):
            continue
        return sorted(group, key=lambda job: _message_order(job.payload))
    return []


def _album_key(payload: dict[str, Any]) -> tuple[int, int, str] | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    group = message.get("media_group_id")
    chat = message.get("chat")
    actor = message.get("from")
    if (
        not isinstance(group, str)
        or not group
        or not isinstance(chat, dict)
        or type(chat.get("id")) is not int
        or not isinstance(actor, dict)
        or type(actor.get("id")) is not int
    ):
        return None
    return (chat["id"], actor["id"], group)


def _message_order(payload: dict[str, Any]) -> tuple[int, int]:
    message = payload.get("message")
    message_id = message.get("message_id") if isinstance(message, dict) else None
    update_id = payload.get("update_id")
    return (
        message_id if type(message_id) is int else _TELEGRAM_ORDER_MAX,
        update_id if type(update_id) is int else _TELEGRAM_ORDER_MAX,
    )


def _caption(inbound: AQUBTransportInbound) -> str:
    message = inbound.payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("caption"), str):
        return str(message["caption"]).strip()[:1_024]
    return ""


def _parse_reply_cursor(value: Any) -> int:
    if not isinstance(value, str) or not value.startswith("reply_cursor:"):
        return 0
    raw = value.removeprefix("reply_cursor:")
    return int(raw) if raw in {"1", "2", "3", "4", "5"} else 0


async def _deliver_reply_messages(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    chat_id: str,
    messages: list[str],
    receipt_ids: list[UUID],
    cursor: int,
) -> None:
    for index, message in enumerate(messages, start=1):
        if index <= cursor:
            continue
        await telegram.send_plain_message(chat_id=chat_id, text=message)
        async with session_factory() as session:
            store = SqlAQUBFoodMediaStore(session)
            for receipt_id in receipt_ids:
                marker = getattr(store, "mark_reply_cursor", None)
                if marker is not None:
                    await marker(receipt_id=str(receipt_id), cursor=index)


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


async def _send_batch_failure(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    telegram: TelegramClient,
    fallback_chat_id: str,
    receipt_ids: list[UUID],
    code: str,
) -> None:
    safe_code = _safe_error_code(AQUBFoodMediaError(code))
    await telegram.send_plain_message(
        chat_id=fallback_chat_id,
        text=(
            "Альбом сохранил, но не смог разобрать ни одной фотографии. "
            "Перешлите только недоступные фото или опишите еду текстом. "
            f"Код диагностики: {safe_code}."
        ),
    )
    await _mark_final_many(session_factory=session_factory, receipt_ids=receipt_ids)


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


async def _mark_final_many(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    receipt_ids: list[UUID],
) -> None:
    async with session_factory() as session:
        store = SqlAQUBFoodMediaStore(session)
        for receipt_id in receipt_ids:
            await store.mark_final_sent(
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


def _reply_messages(downstream: dict[str, Any]) -> list[str]:
    domain = downstream.get("result")
    if not isinstance(domain, dict):
        raise AQUBFoodMediaError("aqub_food_media_downstream_invalid")
    if domain.get("suppress_reply") is True:
        if domain.get("reply_text") is not None or domain.get("reply_messages") is not None:
            raise AQUBFoodMediaError("aqub_food_media_downstream_invalid")
        return []
    messages = domain.get("reply_messages")
    if isinstance(messages, list):
        if not 1 <= len(messages) <= 5 or any(
            not isinstance(value, str) or not value.strip() or len(value.strip()) > 4_096
            for value in messages
        ):
            raise AQUBFoodMediaError("aqub_food_media_downstream_invalid")
        return [value.strip() for value in messages]
    value = domain.get("reply_text")
    if not isinstance(value, str) or not value.strip() or len(value) > 4_096:
        raise AQUBFoodMediaError("aqub_food_media_downstream_invalid")
    return [value.strip()]


def _reply_text(downstream: dict[str, Any]) -> str:
    """Compatibility shim for existing worker tests and callers."""

    return _reply_messages(downstream)[-1]


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
