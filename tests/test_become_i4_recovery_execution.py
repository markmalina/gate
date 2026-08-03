from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from app.application.become_i4_chain import BecomeI4Stage, canonical_digest
from app.infrastructure.become_i4_recovery_execution import (
    SessionFactoryBecomeI4RecoveryExecutor,
    build_stage_event_payload,
    validate_stage_event_replay,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class NoPostClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(
        self,
        url: str,
        *,
        json: object,
        timeout: float,
    ) -> httpx.Response:
        self.calls += 1
        raise AssertionError("business POST must remain unreachable")


@pytest.mark.anyio
async def test_default_off_recovery_executor_never_posts() -> None:
    client = NoPostClient()
    executor = SessionFactoryBecomeI4RecoveryExecutor(
        cast(async_sessionmaker[AsyncSession], object()),
        http_client=client,
        enabled=False,
        max_posts=0,
    )
    result = await executor.run_once(
        workspace_key="mark-personal",
        now=datetime.now(UTC),
    )
    assert result.disposition == "disabled"
    assert result.attempted is False
    assert client.calls == 0


def test_reservation_and_terminal_events_are_exact_replays() -> None:
    reservation = build_stage_event_payload(
        attempt_digest="1" * 64,
        stage=BecomeI4Stage.MAYAKI,
        event_sequence=1,
        event_type="reserved",
        request_digest="2" * 64,
        source_sha="3" * 40,
        receipt_digest=None,
    )
    assert validate_stage_event_replay(
        reservation,
        expected_digest=canonical_digest(reservation),
    ) == reservation

    terminal = build_stage_event_payload(
        attempt_digest="1" * 64,
        stage=BecomeI4Stage.MAYAKI,
        event_sequence=2,
        event_type="completed",
        request_digest="2" * 64,
        source_sha="3" * 40,
        receipt_digest="4" * 64,
    )
    assert validate_stage_event_replay(
        terminal,
        expected_digest=canonical_digest(terminal),
    ) == terminal


@pytest.mark.parametrize(
    "mutation",
    [
        {"request_digest": "5" * 64},
        {"source_sha": "6" * 40},
        {"event_type": "failed"},
        {"receipt_digest": "7" * 64},
        {"external_effects": 1},
        {"unexpected": True},
    ],
)
def test_mutated_stage_event_fails_closed(
    mutation: dict[str, object],
) -> None:
    original = build_stage_event_payload(
        attempt_digest="1" * 64,
        stage=BecomeI4Stage.OKO,
        event_sequence=2,
        event_type="completed",
        request_digest="2" * 64,
        source_sha="3" * 40,
        receipt_digest="4" * 64,
    )
    with pytest.raises(ValueError):
        validate_stage_event_replay(
            {**original, **mutation},
            expected_digest=canonical_digest(original),
        )


def test_terminal_requires_prior_reservation_shape() -> None:
    with pytest.raises(ValueError, match="event_receipt_invalid"):
        build_stage_event_payload(
            attempt_digest="1" * 64,
            stage=BecomeI4Stage.TAKE,
            event_sequence=2,
            event_type="completed",
            request_digest="2" * 64,
            source_sha="3" * 40,
            receipt_digest=None,
        )
    with pytest.raises(ValueError, match="event_order_invalid"):
        build_stage_event_payload(
            attempt_digest="1" * 64,
            stage=BecomeI4Stage.TAKE,
            event_sequence=1,
            event_type="completed",
            request_digest="2" * 64,
            source_sha="3" * 40,
            receipt_digest=None,
        )
