"""Telegram adapter boundary.

The adapter must not contain business logic. It should normalize Telegram updates
and delegate routing/decisioning to Gate Core.
"""

from .models import GateEvent


class TelegramAdapter:
    def normalize_update(self, update: dict) -> GateEvent:
        raise NotImplementedError("Telegram update normalization is not implemented yet.")

    def send_approved_reply(self, chat_id: str, text: str) -> None:
        raise NotImplementedError("Telegram sending is not implemented yet.")
