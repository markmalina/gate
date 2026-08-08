"""Gate persistence and permanent AQUB production binding."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.aqub_s0_preflight import (
    AQUB_BOT_USERNAME,
    AQUBDurableAcceptance,
    AQUBPreflightBinding,
    AQUBPreflightCommand,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
)
from app.application.telegram_processing import canonical_telegram_payload_digest
from app.core.config import Settings
from app.models.domain import TelegramReceipt, TelegramReceiptStatus

AQUB_PREFLIGHT_BOT_KEY = "telegram_aqub_preflight"

# Permanent production binding, pinned from owner-confirmed live evidence on 2026-08-07.
# Provider identifiers stay inside Gate. ACE remains unbound until a real ACE is enrolled.
AQUB_RELEASE_GROUP_CHAT_ID = "-5052983997"
AQUB_RELEASE_ALPHA_USER_ID = "7819816320"
AQUB_RELEASE_ACE_USER_ID = "__unbound_ace__"
AQUB_RELEASE_READY_AT = datetime(2026, 8, 7, 13, 17, tzinfo=UTC)


class SqlAQUBPreflightReceiptStore:
    """Commit an AQUB terminal receipt without entering the common worker queue."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def accept(self, command: AQUBPreflightCommand) -> AQUBDurableAcceptance:
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
                    bot_key=AQUB_PREFLIGHT_BOT_KEY,
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
                        ).where(
                            TelegramReceipt.bot_key == AQUB_PREFLIGHT_BOT_KEY,
                            TelegramReceipt.update_id == update_id,
                        )
                    )
                ).one()
                if (
                    canonical_telegram_payload_digest(existing[1])
                    != command.payload_sha256
                ):
                    raise RuntimeError("aqub_receipt_mutated_replay")
                if (
                    existing[2] != TelegramReceiptStatus.PROCESSED.value
                    or existing[3] is None
                ):
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
            causation_id=f"{AQUB_PREFLIGHT_BOT_KEY}:{update_id}",
        )


def build_aqub_s0_binding(settings: Settings) -> AQUBPreflightBinding:
    """Return the single permanent owner-confirmed AQUB production binding."""

    signing_secret = getattr(settings, "gate_aqub_receipt_signing_key", None)
    if signing_secret is None:
        raise ValueError("AQUB signing key is unavailable")
    signing_key = signing_secret.get_secret_value()
    return AQUBPreflightBinding(
        enabled=True,
        bot_username=AQUB_BOT_USERNAME,
        group_chat_id=AQUB_RELEASE_GROUP_CHAT_ID,
        ace_user_id=AQUB_RELEASE_ACE_USER_ID,
        alpha_user_id=AQUB_RELEASE_ALPHA_USER_ID,
        ready_at=AQUB_RELEASE_READY_AT,
        receipt_signing_key=signing_key.encode(),
    )
