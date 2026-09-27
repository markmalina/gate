from datetime import UTC, datetime, timedelta

import pytest
from app.application.reply_sequence_cooldown import (
    ReplySequenceCooldown,
    extend_reply_sequence_cooldown,
)

NOW = datetime(2026, 9, 27, tzinfo=UTC)


@pytest.mark.parametrize("seconds", [0, 1, 60, 172800])
def test_valid_delays_are_preserved_exactly(seconds: int) -> None:
    result = extend_reply_sequence_cooldown(
        now=NOW, retry_after_seconds=seconds, current=ReplySequenceCooldown(None)
    )
    assert result == ReplySequenceCooldown(NOW + timedelta(seconds=seconds))


@pytest.mark.parametrize("value", [None, True, False, -1, "120", 1.5, {}, []])
def test_malformed_retry_uses_conservative_fallback(value) -> None:
    result = extend_reply_sequence_cooldown(
        now=NOW, retry_after_seconds=value, current=ReplySequenceCooldown(None)
    )
    assert result.blocked_until == NOW + timedelta(seconds=60)


def test_late_shorter_response_cannot_shorten_existing_block() -> None:
    current = ReplySequenceCooldown(NOW + timedelta(hours=1))
    assert (
        extend_reply_sequence_cooldown(now=NOW, retry_after_seconds=10, current=current) == current
    )


def test_unrepresentable_delay_blocks_indefinitely_instead_of_truncating() -> None:
    current = ReplySequenceCooldown(NOW + timedelta(hours=1))
    result = extend_reply_sequence_cooldown(now=NOW, retry_after_seconds=10**100, current=current)
    assert result == ReplySequenceCooldown(current.blocked_until, blocked_indefinitely=True)
    assert extend_reply_sequence_cooldown(now=NOW, retry_after_seconds=1, current=result) == result


def test_timestamp_overflow_also_blocks_indefinitely() -> None:
    result = extend_reply_sequence_cooldown(
        now=datetime.max.replace(tzinfo=UTC),
        retry_after_seconds=1,
        current=ReplySequenceCooldown(None),
    )
    assert result.blocked_indefinitely


def test_naive_clock_and_stored_deadline_rejected() -> None:
    with pytest.raises(ValueError):
        extend_reply_sequence_cooldown(
            now=NOW.replace(tzinfo=None),
            retry_after_seconds=1,
            current=ReplySequenceCooldown(None),
        )
    with pytest.raises(ValueError):
        extend_reply_sequence_cooldown(
            now=NOW,
            retry_after_seconds=1,
            current=ReplySequenceCooldown(NOW.replace(tzinfo=None)),
        )
