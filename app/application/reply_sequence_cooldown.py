"""Pure cooldown arithmetic for the future durable #716 transport budget.

The adapter must merge and persist this result under the account/bot row locks
using database time. This module does not retry or resume a failed sequence.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ReplySequenceCooldown:
    blocked_until: datetime | None
    blocked_indefinitely: bool = False


def extend_reply_sequence_cooldown(
    *,
    now: datetime,
    retry_after_seconds: int | None,
    current: ReplySequenceCooldown,
) -> ReplySequenceCooldown:
    """Monotonically extend a provider cooldown; malformed values use 60 seconds.

    An unrepresentable provider delay fails closed without rounding down. Only
    an explicitly authorized operational action can clear an indefinite block.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Cooldown clock must be timezone-aware")
    if current.blocked_until is not None and (
        current.blocked_until.tzinfo is None or current.blocked_until.utcoffset() is None
    ):
        raise ValueError("Stored cooldown must be timezone-aware")
    if current.blocked_indefinitely:
        return current
    seconds = (
        retry_after_seconds if type(retry_after_seconds) is int and retry_after_seconds >= 0 else 60
    )
    try:
        until = now + timedelta(seconds=seconds)
    except OverflowError:
        return ReplySequenceCooldown(current.blocked_until, blocked_indefinitely=True)
    if current.blocked_until is not None:
        until = max(until, current.blocked_until)
    return ReplySequenceCooldown(until)
