"""Synthetic tests for the default-off personal Telegram transport."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.application.personal_telegram_transport import (
    ExactSendCommand,
    FakePersonalTelegramTransport,
    MemoryPersonalTelegramControlStore,
    PersonalTelegramPolicy,
    PersonalTelegramTransportError,
    ProviderOutcome,
    SendExactPersonalTelegramService,
)

NOW = datetime(2026, 7, 20, 19, 45, tzinfo=UTC)
TOKEN = "synthetic-exact-approval-token-0001"


def _command(
    *,
    text: str = "Точный одобренный текст.",
    idempotency_key: str = "send_exact_synthetic_001",
) -> ExactSendCommand:
    return ExactSendCommand(
        actor_ref="actor_owner",
        session_ref="session_owner_primary",
        peer_ref="peer_selected_synthetic",
        exact_text=text,
        approval_token=TOKEN,
        idempotency_key=idempotency_key,
        now=NOW,
    )


def _ready_store(*, text: str = "Точный одобренный текст.") -> MemoryPersonalTelegramControlStore:
    store = MemoryPersonalTelegramControlStore()
    digest = store.register_exact_approval(
        approval_token=TOKEN,
        actor_ref="actor_owner",
        peer_ref="peer_selected_synthetic",
        exact_text=text,
        expires_at=NOW + timedelta(minutes=10),
    )
    assert digest != TOKEN
    assert TOKEN not in store.approval_token_digests
    return store


def test_every_personal_capability_is_default_off() -> None:
    policy = PersonalTelegramPolicy()

    assert policy.read_dialogs_enabled is False
    assert policy.contact_sync_enabled is False
    assert policy.send_enabled is False

    service = SendExactPersonalTelegramService(
        policy=policy,
        store=_ready_store(),
        transport=FakePersonalTelegramTransport(),
    )
    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_send_disabled",
    ):
        asyncio.run(service.execute(_command()))


def test_exact_approval_is_bound_to_actor_peer_and_text() -> None:
    store = _ready_store()
    service = SendExactPersonalTelegramService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        store=store,
        transport=FakePersonalTelegramTransport(),
    )

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_exact_text_mismatch",
    ):
        asyncio.run(service.execute(_command(text="Изменённый текст.")))


def test_one_exact_send_is_deterministic_and_idempotent() -> None:
    store = _ready_store()
    transport = FakePersonalTelegramTransport()
    service = SendExactPersonalTelegramService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        store=store,
        transport=transport,
    )

    first = asyncio.run(service.execute(_command()))
    replay = asyncio.run(service.execute(_command()))

    assert first.outcome is ProviderOutcome.SENT
    assert first.external_effect_performed is False
    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.deterministic_random_id == first.deterministic_random_id
    assert len(transport.attempts) == 1


def test_idempotency_key_conflict_fails_closed() -> None:
    store = _ready_store()
    service = SendExactPersonalTelegramService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        store=store,
        transport=FakePersonalTelegramTransport(),
    )
    asyncio.run(service.execute(_command()))

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_idempotency_conflict",
    ):
        asyncio.run(
            service.execute(
                _command(
                    text="Другой текст.",
                    idempotency_key="send_exact_synthetic_001",
                )
            )
        )


def test_unknown_outcome_is_recorded_and_never_blindly_retried() -> None:
    store = _ready_store()
    transport = FakePersonalTelegramTransport(raise_unknown=True)
    service = SendExactPersonalTelegramService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        store=store,
        transport=transport,
    )

    first = asyncio.run(service.execute(_command()))
    replay = asyncio.run(service.execute(_command()))

    assert first.outcome is ProviderOutcome.UNKNOWN
    assert first.provider_receipt_ref is None
    assert first.external_effect_performed is None
    assert replay.outcome is ProviderOutcome.UNKNOWN
    assert replay.idempotent_replay is True
    assert len(transport.attempts) == 1


def test_approval_is_single_use_across_different_commands() -> None:
    store = _ready_store()
    service = SendExactPersonalTelegramService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        store=store,
        transport=FakePersonalTelegramTransport(),
    )
    asyncio.run(service.execute(_command()))

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_approval_consumed",
    ):
        asyncio.run(
            service.execute(
                _command(idempotency_key="send_exact_synthetic_002")
            )
        )


def test_raw_peer_identifiers_and_blank_text_are_rejected() -> None:
    store = _ready_store()
    service = SendExactPersonalTelegramService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        store=store,
        transport=FakePersonalTelegramTransport(),
    )
    invalid_peer = _command()
    invalid_peer = ExactSendCommand(
        actor_ref=invalid_peer.actor_ref,
        session_ref=invalid_peer.session_ref,
        peer_ref="375291234567",
        exact_text=invalid_peer.exact_text,
        approval_token=invalid_peer.approval_token,
        idempotency_key=invalid_peer.idempotency_key,
        now=invalid_peer.now,
    )

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_peer_ref_invalid",
    ):
        asyncio.run(service.execute(invalid_peer))

    blank_store = _ready_store()
    blank_service = SendExactPersonalTelegramService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        store=blank_store,
        transport=FakePersonalTelegramTransport(),
    )
    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_exact_text_invalid",
    ):
        asyncio.run(blank_service.execute(_command(text=" ")))


def test_expired_approval_fails_closed() -> None:
    store = MemoryPersonalTelegramControlStore()
    store.register_exact_approval(
        approval_token=TOKEN,
        actor_ref="actor_owner",
        peer_ref="peer_selected_synthetic",
        exact_text="Точный одобренный текст.",
        expires_at=NOW - timedelta(seconds=1),
    )
    service = SendExactPersonalTelegramService(
        policy=PersonalTelegramPolicy(send_enabled=True),
        store=store,
        transport=FakePersonalTelegramTransport(),
    )

    with pytest.raises(
        PersonalTelegramTransportError,
        match="personal_telegram_approval_expired",
    ):
        asyncio.run(service.execute(_command()))
