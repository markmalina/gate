"""One bounded owner-approved AQUB General Cube release replay.

The operation intentionally selects provider identifiers only from Gate
persistence. No raw Telegram identifier or inbound private text is committed,
logged, or returned.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.infrastructure.aqub_general_route import AQUBGeneralRouteStatus
from app.infrastructure.aqub_installation import (
    AQUB_RECEIPT_BOT_KEY,
    aqub_receipt_signing_key,
)
from app.infrastructure.aqub_live import (
    AQUBLiveDeliveryService,
    ParsedAQUBDelivery,
)
from app.models.aqub import AQUBGeneralRoute
from app.models.aqub_live import AQUBLiveDelivery
from app.models.domain import TelegramReceipt, TelegramReceiptStatus
from app.services.telegram import TelegramClient

JsonObject = dict[str, Any]

AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF = (
    "aqub-general-release-replay-20260827"
)
AQUB_GENERAL_RELEASE_REPLAY_TURN_REF = (
    "e786c199-a539-592a-9db4-9ae03e0b7bbc"
)
AQUB_GENERAL_RELEASE_REPLAY_MESSAGES = (
    "Я могу быть тебе полезен как личный помощник прямо в этом Кубе.",
    (
        "Могу искать актуальную информацию с источниками, разбираться в "
        "задачах, помогать с решениями и планами, считать, готовить тексты "
        "и помнить контекст наших разговоров. Просто напиши вопрос или "
        "задачу — разберём."
    ),
)
AQUB_GENERAL_RELEASE_REPLAY_CANDIDATE_SHA256 = (
    "4c9e0604bba4702ae7c2130dca19155b0e76b0afbbb40e5ed58f7b0c1345f705"
)
_SOURCE_WINDOW_START = datetime(2026, 8, 27, 7, 35, tzinfo=UTC)
_SOURCE_CUTOFF = datetime(2026, 8, 27, 7, 36, tzinfo=UTC)
_SOURCE_MAX_GAP_SECONDS = 15
_IDEMPOTENCY_KEYS = (
    "aqub-general-release-replay-20260827:1",
    "aqub-general-release-replay-20260827:2",
)
_COMMAND_IDS = (
    UUID("acf15b46-9af6-56b8-ab06-4c41d3e36136"),
    UUID("01ca32dc-570b-546f-aea9-d43196ed4f0a"),
)


class AQUBGeneralReleaseReplayError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _candidate_digest(messages: Sequence[str]) -> str:
    raw = json.dumps(
        list(messages),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _opaque_ref(signing_key: bytes, kind: str, raw: str) -> str:
    digest = hmac.new(
        signing_key,
        f"{kind}:{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{kind}:{digest}"


def _message_candidate(receipt: TelegramReceipt, group_chat_id: str) -> tuple[int, int] | None:
    payload = receipt.payload
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    sender = message.get("from")
    message_id = message.get("message_id")
    occurred_at = message.get("date")
    text = message.get("text")
    if (
        not isinstance(chat, dict)
        or str(chat.get("id")) != group_chat_id
        or not isinstance(sender, dict)
        or sender.get("is_bot") is not False
        or type(message_id) is not int
        or message_id < 1
        or type(occurred_at) is not int
        or not isinstance(text, str)
        or not text
    ):
        return None
    return message_id, occurred_at


def _select_source_message_id(
    receipts: Sequence[TelegramReceipt],
    *,
    group_chat_id: str,
) -> int:
    candidates = [
        candidate
        for receipt in receipts
        if (candidate := _message_candidate(receipt, group_chat_id)) is not None
    ]
    if len(candidates) < 2:
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_source_missing")
    latest, source = candidates[:2]
    latest_message_id, latest_epoch = latest
    source_message_id, source_epoch = source
    gap = latest_epoch - source_epoch
    if (
        source_message_id >= latest_message_id
        or gap < 1
        or gap > _SOURCE_MAX_GAP_SECONDS
    ):
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_source_ambiguous")
    return source_message_id


async def _active_general_routes(
    session: AsyncSession,
) -> list[AQUBGeneralRoute]:
    routes = (
        await session.scalars(
            select(AQUBGeneralRoute).where(
                AQUBGeneralRoute.bot_key == AQUB_RECEIPT_BOT_KEY,
                AQUBGeneralRoute.status == AQUBGeneralRouteStatus.ACTIVE.value,
            )
        )
    ).all()
    if not routes or any(not isinstance(route, AQUBGeneralRoute) for route in routes):
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_route_ambiguous")
    return list(routes)


async def _source_message_id(
    session: AsyncSession,
    *,
    group_chat_id: str,
) -> int:
    try:
        raw_chat_id = int(group_chat_id)
    except ValueError as exc:
        raise AQUBGeneralReleaseReplayError(
            "aqub_general_replay_route_invalid"
        ) from exc
    receipts = (
        await session.scalars(
            select(TelegramReceipt)
            .where(
                TelegramReceipt.bot_key == AQUB_RECEIPT_BOT_KEY,
                TelegramReceipt.status == TelegramReceiptStatus.PROCESSED.value,
                TelegramReceipt.payload.contains(
                    {"message": {"chat": {"id": raw_chat_id}}}
                ),
            )
            .order_by(TelegramReceipt.update_id.desc())
            .limit(512)
        )
    ).all()
    window_start_epoch = int(_SOURCE_WINDOW_START.timestamp())
    cutoff_epoch = int(_SOURCE_CUTOFF.timestamp())
    windowed_receipts = [
        receipt
        for receipt in receipts
        if (
            candidate := _message_candidate(receipt, group_chat_id)
        ) is not None
        and window_start_epoch <= candidate[1] < cutoff_epoch
    ]
    return _select_source_message_id(
        windowed_receipts,
        group_chat_id=group_chat_id,
    )


async def _release_target(
    session: AsyncSession,
) -> tuple[AQUBGeneralRoute, int]:
    matches: list[tuple[AQUBGeneralRoute, int]] = []
    for route in await _active_general_routes(session):
        try:
            source_message_id = await _source_message_id(
                session,
                group_chat_id=route.group_chat_id,
            )
        except AQUBGeneralReleaseReplayError as exc:
            if exc.code in {
                "aqub_general_replay_source_missing",
                "aqub_general_replay_source_ambiguous",
            }:
                continue
            raise
        matches.append((route, source_message_id))
    if len(matches) != 1:
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_route_ambiguous")
    return matches[0]


def _delivery_command(
    *,
    signing_key: bytes,
    raw_chat_id: str,
    position: int,
    source_message_id: int,
) -> ParsedAQUBDelivery:
    text = AQUB_GENERAL_RELEASE_REPLAY_MESSAGES[position]
    idempotency_key = _IDEMPOTENCY_KEYS[position]
    reply_to_message_id = source_message_id if position == 0 else None
    request_body = {
        "operation": AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF,
        "position": position + 1,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "reply_to_source": position == 0,
    }
    request_sha256 = hashlib.sha256(
        json.dumps(
            request_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return ParsedAQUBDelivery(
        command_id=_COMMAND_IDS[position],
        idempotency_key=idempotency_key,
        chat_ref=_opaque_ref(signing_key, "chat", raw_chat_id),
        assignment_ref="assignment:aqub-general-release-replay-v1",
        step_ref=f"step:{position + 1}",
        text=text,
        request_sha256=request_sha256,
        force_reply=False,
        reply_to_message_id=reply_to_message_id,
    )


async def send_aqub_general_release_replay(
    *,
    session: AsyncSession,
    settings: Settings,
    http_client: httpx.AsyncClient,
    installation_ref: str,
    turn_ref: str,
    expected_candidate_sha256: str,
    confirmed: bool,
    delivery_service_factory: Callable[..., AQUBLiveDeliveryService] = AQUBLiveDeliveryService,
) -> JsonObject:
    if not confirmed:
        raise AQUBGeneralReleaseReplayError(
            "aqub_general_replay_confirmation_required"
        )
    if installation_ref != AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF:
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_selector_mismatch")
    if turn_ref != AQUB_GENERAL_RELEASE_REPLAY_TURN_REF:
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_turn_mismatch")
    if (
        expected_candidate_sha256
        != AQUB_GENERAL_RELEASE_REPLAY_CANDIDATE_SHA256
        or _candidate_digest(AQUB_GENERAL_RELEASE_REPLAY_MESSAGES)
        != AQUB_GENERAL_RELEASE_REPLAY_CANDIDATE_SHA256
    ):
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_digest_mismatch")

    route, source_message_id = await _release_target(session)
    signing_key = aqub_receipt_signing_key(settings)
    token = settings.telegram_aqub_bot_token.get_secret_value().strip()
    if not token:
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_token_missing")
    telegram = TelegramClient(token, http_client=http_client)
    service = delivery_service_factory(
        session=session,
        telegram=telegram,
        signing_key=signing_key,
        raw_chat_id=route.group_chat_id,
    )

    results: list[JsonObject] = []
    for position in range(len(AQUB_GENERAL_RELEASE_REPLAY_MESSAGES)):
        result = await service.deliver(
            _delivery_command(
                signing_key=signing_key,
                raw_chat_id=route.group_chat_id,
                position=position,
                source_message_id=source_message_id,
            )
        )
        results.append(result)
        if result.get("terminal", {}).get("status") != "sent":
            break

    sent = (
        len(results) == len(AQUB_GENERAL_RELEASE_REPLAY_MESSAGES)
        and all(result.get("terminal", {}).get("status") == "sent" for result in results)
    )
    replay = bool(results) and all(
        result.get("ack", {}).get("status") == "duplicate" for result in results
    )
    return {
        "installation_ref": AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF,
        "turn_ref": AQUB_GENERAL_RELEASE_REPLAY_TURN_REF,
        "state": "sent" if sent else "failed",
        "replay": replay,
        "provider_message_id_exposed": False,
        "participant_identities_exposed": False,
    }


async def verify_aqub_general_release_replay(
    *,
    session: AsyncSession,
    installation_ref: str,
    turn_ref: str,
    expected_candidate_sha256: str,
) -> JsonObject:
    if installation_ref != AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF:
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_selector_mismatch")
    if turn_ref != AQUB_GENERAL_RELEASE_REPLAY_TURN_REF:
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_turn_mismatch")
    if expected_candidate_sha256 != AQUB_GENERAL_RELEASE_REPLAY_CANDIDATE_SHA256:
        raise AQUBGeneralReleaseReplayError("aqub_general_replay_digest_mismatch")

    rows = (
        await session.scalars(
            select(AQUBLiveDelivery).where(
                AQUBLiveDelivery.idempotency_key.in_(_IDEMPOTENCY_KEYS)
            )
        )
    ).all()
    states = {row.idempotency_key: row.state for row in rows}
    if len(states) != len(_IDEMPOTENCY_KEYS):
        state = "blocked"
    elif all(states[key] == "sent" for key in _IDEMPOTENCY_KEYS):
        state = "sent"
    elif any(states[key] == "unknown" for key in _IDEMPOTENCY_KEYS):
        state = "unknown"
    elif any(states[key] == "sending" for key in _IDEMPOTENCY_KEYS):
        state = "sending"
    elif any(states[key] == "pending" for key in _IDEMPOTENCY_KEYS):
        state = "reserved"
    else:
        state = "failed"
    return {
        "installation_ref": AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF,
        "turn_ref": AQUB_GENERAL_RELEASE_REPLAY_TURN_REF,
        "state": state,
        "replay": True,
        "provider_message_id_exposed": False,
        "participant_identities_exposed": False,
    }
