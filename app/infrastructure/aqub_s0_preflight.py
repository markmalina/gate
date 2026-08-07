"""Gate persistence and safe configuration for the AQUB S0 preflight."""

from __future__ import annotations

from datetime import datetime

from app.application.aqub_s0_preflight import (
    AQUBDurableAcceptance,
    AQUBPreflightBinding,
    AQUBPreflightCommand,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
)
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    AcceptTelegramReceiptService,
    TelegramReceiptStore,
)
from app.core.config import Settings

AQUB_PREFLIGHT_BOT_KEY = "telegram_aqub_preflight"


class GateAQUBPreflightStore:
    """Adapt the existing atomic Telegram receipt/outbox store for AQUB."""

    def __init__(self, receipt_store: TelegramReceiptStore) -> None:
        self._receipt_store = receipt_store

    async def accept(self, command: AQUBPreflightCommand) -> AQUBDurableAcceptance:
        try:
            update_id = int(command.external_id)
        except ValueError as exc:
            raise RuntimeError("aqub_external_id_invalid") from exc
        acceptance = await AcceptTelegramReceiptService(self._receipt_store).execute(
            AcceptTelegramReceiptCommand(
                bot_key=AQUB_PREFLIGHT_BOT_KEY,
                update_id=update_id,
                payload=dict(command.payload),
                received_at=command.received_at,
            )
        )
        return AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=(
                AQUBStoreDisposition.DUPLICATE
                if acceptance.duplicate
                else AQUBStoreDisposition.CREATED
            ),
            command_receipt_id=acceptance.receipt_id,
            root_event_id=acceptance.outbox_event_id,
            correlation_id=acceptance.receipt_id,
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
