"""Persistence-backed AQUB live Telegram delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.aqub_live import AQUB_DELIVERY_COMMAND_SCHEMA
from app.models.aqub_live import AQUBLiveDelivery
from app.services.telegram import TelegramApiError, TelegramClient

JsonObject = dict[str, Any]
MAX_TELEGRAM_TEXT = 4096
SENDING_UNKNOWN_AFTER = timedelta(seconds=30)
_RETRYABLE_TELEGRAM_ERROR = re.compile(r"^telegram_(?:[0-9]+|error)$")


class AQUBLiveDeliveryError(ValueError):
    def __init__(self, code: str, *, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ParsedAQUBDelivery:
    command_id: UUID
    idempotency_key: str
    chat_ref: str
    assignment_ref: str
    step_ref: str
    text: str
    request_sha256: str
    force_reply: bool = False
    reply_to_message_id: int | None = None


def verify_aqub_signature(*, raw_body: bytes, signature: str, signing_key: bytes) -> None:
    if not signing_key:
        raise AQUBLiveDeliveryError("aqub_signing_key_missing", status_code=503)
    expected = hmac.new(signing_key, raw_body, hashlib.sha256).hexdigest()
    try:
        valid = hmac.compare_digest(signature.encode(), expected.encode())
    except UnicodeError:
        valid = False
    if not valid:
        raise AQUBLiveDeliveryError("aqub_bad_signature", status_code=401)


def parse_aqub_delivery(
    raw_body: bytes,
    *,
    signing_key: bytes,
    raw_chat_id: str,
) -> ParsedAQUBDelivery:
    if len(raw_body) > 64 * 1024:
        raise AQUBLiveDeliveryError("aqub_delivery_too_large", status_code=413)
    try:
        value: Any = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AQUBLiveDeliveryError("aqub_delivery_bad_json") from exc
    if not isinstance(value, dict) or value.get("schema_version") != AQUB_DELIVERY_COMMAND_SCHEMA:
        raise AQUBLiveDeliveryError("aqub_delivery_bad_schema")

    try:
        command_id = UUID(str(value.get("command_id")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AQUBLiveDeliveryError("aqub_delivery_bad_command_id") from exc
    idempotency_key = value.get("idempotency_key")
    chat_ref = value.get("chat_ref")
    assignment_ref = value.get("assignment_ref")
    step_ref = value.get("step_ref")
    text_value = value.get("text")
    force_reply = value.get("force_reply", False)
    reply_to_message_id = value.get("reply_to_message_id")
    if not isinstance(idempotency_key, str) or not (1 <= len(idempotency_key) <= 255):
        raise AQUBLiveDeliveryError("aqub_delivery_bad_idempotency_key")
    if not isinstance(chat_ref, str) or not chat_ref:
        raise AQUBLiveDeliveryError("aqub_delivery_bad_chat_ref")
    if not raw_chat_id:
        raise AQUBLiveDeliveryError("aqub_delivery_binding_missing", status_code=503)
    expected_chat_ref = _opaque_ref(signing_key, "chat", raw_chat_id)
    if chat_ref != expected_chat_ref:
        raise AQUBLiveDeliveryError("aqub_delivery_chat_mismatch", status_code=403)
    if not isinstance(assignment_ref, str) or not assignment_ref:
        raise AQUBLiveDeliveryError("aqub_delivery_bad_assignment_ref")
    if not isinstance(step_ref, str) or not step_ref:
        raise AQUBLiveDeliveryError("aqub_delivery_bad_step_ref")
    if not isinstance(text_value, str) or not (1 <= len(text_value) <= MAX_TELEGRAM_TEXT):
        raise AQUBLiveDeliveryError("aqub_delivery_bad_text")
    if type(force_reply) is not bool:
        raise AQUBLiveDeliveryError("aqub_delivery_bad_force_reply")
    if reply_to_message_id is not None and (
        type(reply_to_message_id) is not int
        or reply_to_message_id < 1
        or reply_to_message_id > (1 << 52) - 1
    ):
        raise AQUBLiveDeliveryError("aqub_delivery_bad_reply_to_message_id")
    return ParsedAQUBDelivery(
        command_id=command_id,
        idempotency_key=idempotency_key,
        chat_ref=chat_ref,
        assignment_ref=assignment_ref,
        step_ref=step_ref,
        text=text_value,
        request_sha256=hashlib.sha256(raw_body).hexdigest(),
        force_reply=force_reply,
        reply_to_message_id=reply_to_message_id,
    )


class AQUBLiveDeliveryService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        telegram: TelegramClient,
        signing_key: bytes,
        raw_chat_id: str,
    ) -> None:
        if not raw_chat_id:
            raise ValueError("AQUB raw chat binding is required")
        self._session = session
        self._telegram = telegram
        self._signing_key = signing_key
        self._raw_chat_id = raw_chat_id

    async def deliver(self, command: ParsedAQUBDelivery) -> JsonObject:
        row_id = uuid4()
        statement: Any = insert(AQUBLiveDelivery).values(
            id=row_id,
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            request_sha256=command.request_sha256,
            chat_ref=command.chat_ref,
            assignment_ref=command.assignment_ref,
            step_ref=command.step_ref,
            text_sha256=hashlib.sha256(command.text.encode()).hexdigest(),
            state="pending",
        )
        statement = statement.on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(
            AQUBLiveDelivery.id
        )
        inserted_id = await self._session.scalar(statement)
        created = inserted_id is not None
        await self._session.commit()

        row = await self._load(command.idempotency_key)
        if row is None:
            raise RuntimeError("aqub_delivery_reservation_missing")
        if row.request_sha256 != command.request_sha256 or row.command_id != command.command_id:
            raise AQUBLiveDeliveryError(
                "aqub_delivery_mutated_replay",
                status_code=409,
            )
        if row.state == "sent":
            return self._settled_response(row, duplicate=True)
        if row.state == "unknown":
            return self._settled_response(row, duplicate=True)
        if row.state == "failed":
            if not _retryable_provider_failure(row.error_code):
                return self._settled_response(row, duplicate=True)
            await self._session.execute(
                update(AQUBLiveDelivery)
                .where(
                    AQUBLiveDelivery.id == row.id,
                    AQUBLiveDelivery.state == "failed",
                )
                .values(
                    state="pending",
                    provider_message_id=None,
                    error_code=None,
                    settled_at=None,
                    updated_at=datetime.now(UTC),
                )
            )
            await self._session.commit()
            row = await self._load(command.idempotency_key)
            if row is None:
                raise RuntimeError("aqub_delivery_retry_missing")
        if row.state == "sending":
            now = datetime.now(UTC)
            updated_at = row.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=UTC)
            if now - updated_at < SENDING_UNKNOWN_AFTER:
                raise AQUBLiveDeliveryError(
                    "aqub_delivery_in_progress",
                    status_code=409,
                )
            await self._session.execute(
                update(AQUBLiveDelivery)
                .where(
                    AQUBLiveDelivery.id == row.id,
                    AQUBLiveDelivery.state == "sending",
                )
                .values(
                    state="unknown",
                    error_code="send_outcome_unknown",
                    settled_at=now,
                )
            )
            await self._session.commit()
            row = await self._load(command.idempotency_key)
            if row is None:
                raise RuntimeError("aqub_delivery_unknown_missing")
            return self._settled_response(row, duplicate=True)

        claimed = await self._session.execute(
            update(AQUBLiveDelivery)
            .where(
                AQUBLiveDelivery.id == row.id,
                AQUBLiveDelivery.state == "pending",
            )
            .values(state="sending", updated_at=datetime.now(UTC))
            .returning(AQUBLiveDelivery.id)
        )
        if claimed.scalar_one_or_none() is None:
            await self._session.rollback()
            raise AQUBLiveDeliveryError(
                "aqub_delivery_in_progress",
                status_code=409,
            )
        await self._session.commit()

        try:
            if command.force_reply:
                response = await self._telegram.send_message(
                    chat_id=self._raw_chat_id,
                    text=command.text,
                    reply_markup={"force_reply": True, "selective": True},
                    reply_to_message_id=command.reply_to_message_id,
                )
            else:
                response = await self._telegram.send_plain_message(
                    chat_id=self._raw_chat_id,
                    text=command.text,
                    reply_to_message_id=command.reply_to_message_id,
                )
            message_id = _telegram_message_id(response)
        except TelegramApiError as exc:
            code = f"telegram_{exc.error_code or exc.http_status or 'error'}"
            await self._settle_failed(row.id, code)
            failed = await self._load(command.idempotency_key)
            if failed is None:
                raise RuntimeError("aqub_delivery_failed_missing") from exc
            return self._settled_response(failed, duplicate=not created)
        except Exception as exc:
            await self._settle_unknown(row.id, "send_outcome_unknown")
            unknown = await self._load(command.idempotency_key)
            if unknown is None:
                raise RuntimeError("aqub_delivery_unknown_missing") from exc
            return self._settled_response(unknown, duplicate=not created)

        await self._session.execute(
            update(AQUBLiveDelivery)
            .where(
                AQUBLiveDelivery.id == row.id,
                AQUBLiveDelivery.state == "sending",
            )
            .values(
                state="sent",
                provider_message_id=str(message_id),
                error_code=None,
                settled_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await self._session.commit()
        sent = await self._load(command.idempotency_key)
        if sent is None:
            raise RuntimeError("aqub_delivery_sent_missing")
        return self._settled_response(sent, duplicate=not created)

    async def _load(self, idempotency_key: str) -> AQUBLiveDelivery | None:
        value: Any = await self._session.scalar(
            select(AQUBLiveDelivery).where(AQUBLiveDelivery.idempotency_key == idempotency_key)
        )
        if value is None:
            return None
        if not isinstance(value, AQUBLiveDelivery):
            raise RuntimeError("aqub_delivery_row_type_invalid")
        return value

    async def _settle_failed(self, row_id: UUID, error_code: str) -> None:
        await self._session.execute(
            update(AQUBLiveDelivery)
            .where(
                AQUBLiveDelivery.id == row_id,
                AQUBLiveDelivery.state == "sending",
            )
            .values(
                state="failed",
                error_code=error_code,
                settled_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await self._session.commit()

    async def _settle_unknown(self, row_id: UUID, error_code: str) -> None:
        await self._session.execute(
            update(AQUBLiveDelivery)
            .where(
                AQUBLiveDelivery.id == row_id,
                AQUBLiveDelivery.state == "sending",
            )
            .values(
                state="unknown",
                error_code=error_code,
                settled_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await self._session.commit()

    def _settled_response(
        self,
        row: AQUBLiveDelivery,
        *,
        duplicate: bool,
    ) -> JsonObject:
        ack_status = "duplicate" if duplicate else "committed"
        ack: JsonObject = {
            "status": ack_status,
            "idempotency_key": row.idempotency_key,
            "command_receipt_ref": _opaque_ref(
                self._signing_key,
                "delivery_command",
                str(row.id),
            ),
        }
        if row.state == "sent" and row.provider_message_id:
            terminal: JsonObject = {
                "status": "sent",
                "idempotency_key": row.idempotency_key,
                "terminal_receipt_ref": _opaque_ref(
                    self._signing_key,
                    "delivery_terminal",
                    f"{row.id}:sent",
                ),
                "delivered_message_ref": _opaque_ref(
                    self._signing_key,
                    "message",
                    row.provider_message_id,
                ),
            }
        else:
            terminal = {
                "status": "failed",
                "idempotency_key": row.idempotency_key,
                "terminal_receipt_ref": _opaque_ref(
                    self._signing_key,
                    "delivery_terminal",
                    f"{row.id}:{row.state}",
                ),
                "reason": row.error_code or row.state,
            }
        return {"ack": ack, "terminal": terminal}


def _retryable_provider_failure(error_code: str | None) -> bool:
    return bool(error_code and _RETRYABLE_TELEGRAM_ERROR.fullmatch(error_code))


def _telegram_message_id(response: JsonObject) -> int:
    if response.get("ok") is not True:
        raise RuntimeError("telegram_send_not_ok")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("telegram_send_message_id_missing")
    message_id = result.get("message_id")
    if type(message_id) is not int:
        raise RuntimeError("telegram_send_message_id_missing")
    return message_id


def _opaque_ref(signing_key: bytes, kind: str, raw: str) -> str:
    digest = hmac.new(
        signing_key,
        f"{kind}:{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{kind}:{digest}"
