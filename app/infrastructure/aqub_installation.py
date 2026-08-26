"""Gate persistence and durable AQUB production installation binding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUB_PING_COMMAND,
    AQUBDurableAcceptance,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
    AQUBTransportBinding,
    AQUBTransportCommand,
)
from app.application.telegram_processing import canonical_telegram_payload_digest
from app.core.config import Settings
from app.models.aqub import AQUBInstallationBinding
from app.models.domain import TelegramReceipt, TelegramReceiptStatus

AQUB_RECEIPT_BOT_KEY = "telegram_aqub_preflight"
AQUB_UNBOUND_ACE_USER_ID = "__unbound_ace__"


@dataclass(frozen=True, slots=True)
class AQUBBindingEvidence:
    receipt_id: UUID
    group_chat_id: str
    alpha_user_id: str
    bound_at: datetime


class SqlAQUBTransportReceiptStore:
    """Commit an AQUB terminal receipt without entering the common worker queue."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def accept(self, command: AQUBTransportCommand) -> AQUBDurableAcceptance:
        try:
            update_id = int(command.external_id)
        except ValueError as exc:
            raise RuntimeError("aqub_external_id_invalid") from exc
        payload = dict(command.payload)
        if canonical_telegram_payload_digest(payload) != command.payload_sha256:
            raise RuntimeError("aqub_payload_digest_mismatch")
        receipt_id = uuid4()
        try:
            inserted_id = await self._session.scalar(
                insert(TelegramReceipt)
                .values(
                    id=receipt_id,
                    bot_key=AQUB_RECEIPT_BOT_KEY,
                    update_id=update_id,
                    payload=payload,
                    status=TelegramReceiptStatus.PROCESSED.value,
                    received_at=command.received_at,
                    processed_at=command.received_at,
                )
                .on_conflict_do_nothing(index_elements=["bot_key", "update_id"])
                .returning(TelegramReceipt.id)
            )
            disposition = AQUBStoreDisposition.CREATED
            if inserted_id is None:
                existing = (
                    await self._session.execute(
                        select(
                            TelegramReceipt.id,
                            TelegramReceipt.payload,
                            TelegramReceipt.status,
                            TelegramReceipt.processed_at,
                            TelegramReceipt.processing_started_at,
                        ).where(
                            TelegramReceipt.bot_key == AQUB_RECEIPT_BOT_KEY,
                            TelegramReceipt.update_id == update_id,
                        )
                    )
                ).one()
                if (
                    canonical_telegram_payload_digest(existing[1])
                    != command.payload_sha256
                ):
                    raise RuntimeError("aqub_receipt_mutated_replay")
                terminal_replay = (
                    existing[2] == TelegramReceiptStatus.PROCESSED.value
                    and existing[3] is not None
                )
                linked_replay = (
                    existing[2] == TelegramReceiptStatus.PROCESSING.value
                    and existing[4] is not None
                )
                if not terminal_replay and not linked_replay:
                    raise RuntimeError("aqub_receipt_not_terminal")
                inserted_id = existing[0]
                disposition = AQUBStoreDisposition.DUPLICATE
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

        durable_id = str(inserted_id)
        return AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=disposition,
            command_receipt_id=durable_id,
            root_event_id=durable_id,
            correlation_id=durable_id,
            causation_id=f"{AQUB_RECEIPT_BOT_KEY}:{update_id}",
        )


def aqub_receipt_signing_key(settings: Settings) -> bytes:
    """Return the permanent AQUB transport signing key without provider IDs."""

    secret: object = getattr(settings, "gate_aqub_receipt_signing_key", None)
    if not isinstance(secret, SecretStr):
        raise ValueError("AQUB signing key is unavailable")
    signing_key = secret.get_secret_value().strip()
    encoded = signing_key.encode()
    if len(encoded) < 32:
        raise ValueError("AQUB signing key is unavailable")
    return encoded


def _message_mapping(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    message = payload.get("message")
    return message if isinstance(message, Mapping) else None


def binding_evidence_from_receipt(receipt: TelegramReceipt) -> AQUBBindingEvidence | None:
    """Extract owner-confirmed binding evidence from a historical accepted ping."""

    if (
        receipt.bot_key != AQUB_RECEIPT_BOT_KEY
        or receipt.status != TelegramReceiptStatus.PROCESSED.value
        or receipt.processed_at is None
    ):
        return None
    message = _message_mapping(receipt.payload)
    if message is None:
        return None
    text = message.get("text")
    if not isinstance(text, str):
        return None
    token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if token.split("@", maxsplit=1)[0] != AQUB_PING_COMMAND:
        return None

    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
        return None
    if chat.get("type") not in {"group", "supergroup"} or sender.get("is_bot") is True:
        return None
    chat_id = chat.get("id")
    sender_id = sender.get("id")
    if (
        not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or not isinstance(sender_id, int)
        or isinstance(sender_id, bool)
    ):
        return None
    return AQUBBindingEvidence(
        receipt_id=receipt.id,
        group_chat_id=str(chat_id),
        alpha_user_id=str(sender_id),
        bound_at=receipt.processed_at,
    )


async def _stored_binding(
    session: AsyncSession,
) -> AQUBInstallationBinding | None:
    value = await session.scalar(
        select(AQUBInstallationBinding).where(
            AQUBInstallationBinding.bot_key == AQUB_RECEIPT_BOT_KEY
        )
    )
    return value if isinstance(value, AQUBInstallationBinding) else None


async def _latest_historical_evidence(
    session: AsyncSession,
) -> AQUBBindingEvidence | None:
    receipts = (
        await session.scalars(
            select(TelegramReceipt)
            .where(
                TelegramReceipt.bot_key == AQUB_RECEIPT_BOT_KEY,
                TelegramReceipt.status == TelegramReceiptStatus.PROCESSED.value,
                TelegramReceipt.processed_at.is_not(None),
            )
            .order_by(TelegramReceipt.processed_at.desc(), TelegramReceipt.id.desc())
            .limit(64)
        )
    ).all()
    for receipt in receipts:
        evidence = binding_evidence_from_receipt(receipt)
        if evidence is not None:
            return evidence
    return None


async def load_aqub_production_binding(
    session: AsyncSession,
    settings: Settings,
) -> AQUBTransportBinding:
    """Load or atomically bootstrap the permanent AQUB installation binding.

    Bootstrap is allowed only from a historical terminal AQUB receipt. Such a
    receipt could exist only after the former exclusive binding had already
    accepted the provider update, so a new arbitrary actor/group cannot claim
    ownership merely by being the first request after deployment.
    """

    signing_key = aqub_receipt_signing_key(settings)
    stored = await _stored_binding(session)
    if stored is None:
        evidence = await _latest_historical_evidence(session)
        if evidence is None:
            raise ValueError("AQUB installation binding evidence is unavailable")
        await session.execute(
            insert(AQUBInstallationBinding)
            .values(
                id=uuid4(),
                bot_key=AQUB_RECEIPT_BOT_KEY,
                group_chat_id=evidence.group_chat_id,
                alpha_user_id=evidence.alpha_user_id,
                evidence_receipt_id=evidence.receipt_id,
                bound_at=evidence.bound_at,
            )
            .on_conflict_do_nothing()
        )
        await session.commit()
        stored = await _stored_binding(session)
    if stored is None:
        raise RuntimeError("AQUB installation binding bootstrap failed")

    return AQUBTransportBinding(
        enabled=True,
        bot_username=AQUB_BOT_USERNAME,
        group_chat_id=stored.group_chat_id,
        ace_user_id=AQUB_UNBOUND_ACE_USER_ID,
        alpha_user_id=stored.alpha_user_id,
        ready_at=stored.bound_at,
        receipt_signing_key=signing_key,
    )
