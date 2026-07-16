"""Boundary proof for one-attempt Telegram Alpha delivery text."""

import pytest

from app.application.alpha_delivery import (
    MAX_TELEGRAM_TEXT_CHARS,
    AlphaDeliveryReservation,
)
from app.application.alpha_group import AgentSendAttemptState


def _reservation(text: str) -> AlphaDeliveryReservation:
    return AlphaDeliveryReservation(
        attempt_key="attempt-telegram-limit-0000000000000001",
        state=AgentSendAttemptState.SENDING,
        target_chat_id="-100730000001",
        candidate_text=text,
    )


def test_exact_telegram_text_limit_is_accepted() -> None:
    reservation = _reservation("а" * MAX_TELEGRAM_TEXT_CHARS)

    assert reservation.candidate_text is not None
    assert len(reservation.candidate_text) == 4_096


def test_text_above_telegram_limit_is_rejected_before_provider_call() -> None:
    with pytest.raises(ValueError, match="one Telegram text message"):
        _reservation("а" * (MAX_TELEGRAM_TEXT_CHARS + 1))
