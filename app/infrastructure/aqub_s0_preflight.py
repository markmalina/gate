"""Gate persistence and safe configuration for the AQUB S0 preflight."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.aqub_s0_preflight import (
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
    """Construct an enabled binding only when every release-pinned value exists."""

    if not settings.gate_aqub_s0_preflight_enabled:
        return AQUBPreflightBinding()
    ready_text = settings.gate_aqub_s0_ready_at.strip()
    try:
        ready_at = datetime.fromisoformat(ready_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("aqub_ready_at_invalid") from exc
    return AQUBPreflightBinding(
        enabled=True,
        bot_username=settings.telegram_aqub_bot_username,
        group_chat_id=settings.gate_aqub_group_chat_id.get_secret_value(),
        ace_user_id=settings.gate_aqub_ace_user_id.get_secret_value(),
        alpha_user_id=settings.gate_aqub_alpha_user_id.get_secret_value(),
        ready_at=ready_at,
        receipt_signing_key=(
            settings.gate_aqub_receipt_signing_key.get_secret_value().encode()
        ),
    )
