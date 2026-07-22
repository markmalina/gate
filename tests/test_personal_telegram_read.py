"""Synthetic tests for the bounded personal Telegram read contract."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.application.personal_telegram_read import (
    FakePersonalTelegramReadTransport,
    MemoryPersonalTelegramReadControlStore,
    MessageDirection,
    PersonalTelegramMetadataEnvelope,
    ReadMetadataCommand,
    ReadOutcome,
    ReadPersonalTelegramMetadataService,
)
from app.application.personal_telegram_transport import (
    PersonalTelegramPolicy,
    PersonalTelegramTransportError,
)

NOW = datetime(2026, 7, 22, 15, 18, tzinfo=UTC)
NOT_BEFORE = NOW - timedelta(hours=1)
TOKEN = "synthetic-read-grant-token-0001"


def _command(
    *,
    idempotency_key: str = "read_synthetic_001",
    peer_ref: str = "peer_selected_synthetic",
    not_before: datetime = NOT_BEFORE,
    limit: int = 5,
) -> ReadMetadataCommand:
    return ReadMetadataCommand(
        actor_ref="actor_owner",
        session_ref="session_owner_primary",
        peer_ref=peer_ref,
        grant_token=TOKEN,
        idempotency_key=idempotency_key,
        not_before=not_before,
        limit=limit,
        now=NOW,
    )


def _store(
    *,
    peer_ref: str = "peer_selected_synthetic",
    not_before: datetime = NOT_BEFORE,
    max_messages: int = 5,
    expires_at: datetime = NOW + timedelta(minutes=10),
) -> MemoryPersonalTelegramReadControlStore:
    store = MemoryPersonalTelegramReadControlStore()
    digest = store.register_grant(
        grant_token=TOKEN,
        actor_ref="actor_owner",
        session_ref="session_owner_primary",
        peer_ref=peer_ref,
        not_before=not_before,
        max_messages=max_messages,
        expires_at=expires_at,
    )
    assert digest != TOKEN
    assert TOKEN not in store.grant_token_digests
    return store


def _service(
    *,
    policy: PersonalTelegramPolicy | None = None,
    store: MemoryPersonalTelegramReadControlStore | None = None,
    transport: FakePersonalTelegramReadTransport | None = None,
) -> ReadPersonalTelegramMetadataService:
    return ReadPersonalTelegramMetadataService(
        policy=policy or PersonalTelegramPolicy(read_dialogs_enabled=True),
        store=store or _store(),
        transport=transport or FakePersonalTelegramReadTransport(),
    )


def test_read_capability_is_default_off() -> None:
    transport = FakePersonalTelegramReadTransport()
    service = _service(policy=PersonalTelegramPolicy(), transport=transport)

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_read_disabled",
    ):
        asyncio.run(service.execute(_command()))

    assert transport.attempts == []


def test_one_shot_metadata_read_is_redacted_and_idempotent() -> None:
    envelopes = (
        PersonalTelegramMetadataEnvelope(
            event_ref="event_synthetic_001",
            occurred_at=NOW - timedelta(minutes=20),
            direction=MessageDirection.INBOUND,
        ),
        PersonalTelegramMetadataEnvelope(
            event_ref="event_synthetic_002",
            occurred_at=NOW - timedelta(minutes=10),
            direction=MessageDirection.OUTBOUND,
        ),
    )
    transport = FakePersonalTelegramReadTransport(envelopes=envelopes)
    service = _service(transport=transport)

    first = asyncio.run(service.execute(_command()))
    replay = asyncio.run(service.execute(_command()))

    assert first.outcome is ReadOutcome.OBSERVED
    assert first.envelope_count == 2
    assert first.envelope_digest is not None
    assert len(first.envelope_digest) == 64
    assert "event_synthetic" not in repr(first)
    assert first.provider_state_mutated is False
    assert first.read_capability_active is False
    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.envelope_digest == first.envelope_digest
    assert len(transport.attempts) == 1


@pytest.mark.parametrize(
    ("not_before", "limit", "error"),
    [
        (NOW - timedelta(hours=25), 5, "personal_telegram_read_window_invalid"),
        (NOT_BEFORE, 6, "personal_telegram_read_limit_invalid"),
        (NOW, 5, "personal_telegram_read_window_invalid"),
    ],
)
def test_window_and_limit_are_bounded(
    not_before: datetime,
    limit: int,
    error: str,
) -> None:
    service = _service()

    with pytest.raises(PersonalTelegramTransportError, match=error):
        asyncio.run(
            service.execute(
                _command(not_before=not_before, limit=limit)
            )
        )


def test_exact_peer_scope_and_one_use_grant_fail_closed() -> None:
    store = _store()
    service = _service(store=store)
    asyncio.run(service.execute(_command()))

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_read_grant_consumed",
    ):
        asyncio.run(
            service.execute(
                _command(
                    idempotency_key="read_synthetic_002",
                )
            )
        )

    other_store = _store()
    other_service = _service(store=other_store)
    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_read_scope_mismatch",
    ):
        asyncio.run(
            other_service.execute(
                _command(
                    idempotency_key="read_synthetic_003",
                    peer_ref="peer_other_synthetic",
                )
            )
        )


def test_expired_grant_fails_before_transport() -> None:
    transport = FakePersonalTelegramReadTransport()
    service = _service(
        store=_store(expires_at=NOW - timedelta(seconds=1)),
        transport=transport,
    )

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_read_grant_expired",
    ):
        asyncio.run(service.execute(_command()))

    assert transport.attempts == []


def test_unknown_provider_outcome_is_not_retried() -> None:
    transport = FakePersonalTelegramReadTransport(raise_unknown=True)
    service = _service(transport=transport)

    first = asyncio.run(service.execute(_command()))
    replay = asyncio.run(service.execute(_command()))

    assert first.outcome is ReadOutcome.UNKNOWN
    assert first.envelope_count == 0
    assert first.envelope_digest is None
    assert first.provider_state_mutated is None
    assert replay.idempotent_replay is True
    assert len(transport.attempts) == 1


def test_provider_state_mutation_fails_closed_and_is_not_retried() -> None:
    transport = FakePersonalTelegramReadTransport(
        provider_state_mutated=True,
    )
    service = _service(transport=transport)

    first = asyncio.run(service.execute(_command()))
    replay = asyncio.run(service.execute(_command()))

    assert first.outcome is ReadOutcome.UNKNOWN
    assert first.read_capability_active is False
    assert replay.idempotent_replay is True
    assert len(transport.attempts) == 1


def test_idempotency_conflict_fails_closed() -> None:
    service = _service()
    asyncio.run(service.execute(_command()))

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_read_idempotency_conflict",
    ):
        asyncio.run(
            service.execute(
                _command(
                    peer_ref="peer_other_synthetic",
                )
            )
        )
