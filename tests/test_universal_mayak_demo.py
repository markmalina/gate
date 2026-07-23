from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from app.application.universal_mayak_demo import (
    DemoContextMessage,
    DemoDeliveryReceipt,
    DemoDeliveryState,
    DemoDisposition,
    DemoInstallationState,
    DemoInvocation,
    DemoReservationState,
    DemoScope,
    DemoTerminalReceipt,
    DemoTurnReservation,
    ProcessUniversalMayakCommand,
    ProcessUniversalMayakDemoService,
    UniversalMayakDemoError,
    UniversalMayakInstallation,
    UniversalMayakModelReply,
)

NOW = datetime(2026, 7, 23, 7, 0, tzinfo=UTC)
SCOPE = DemoScope("workspace_owner", "installation_demo", "dialog_owner")


class Store:
    def __init__(self, installation: UniversalMayakInstallation | None = None):
        self.installation = installation or UniversalMayakInstallation(
            SCOPE,
            "Маяк",
            "цифровой помощник",
            state=DemoInstallationState.ACTIVE,
        )
        self.turns: dict[str, tuple[str, str, DemoTerminalReceipt | None]] = {}
        self.context_by_dialog: dict[str, tuple[DemoContextMessage, ...]] = {}
        self.staged: dict[str, str] = {}
        self.transitions: list[DemoInstallationState] = []

    async def resolve_installation(self, scope):
        return self.installation

    async def reserve_turn(self, command, *, input_sha256):
        current = self.turns.get(command.idempotency_key)
        if current is None:
            turn_ref = f"turn_{len(self.turns) + 1}"
            self.turns[command.idempotency_key] = (input_sha256, turn_ref, None)
            return DemoTurnReservation(
                DemoReservationState.NEW,
                turn_ref,
                input_sha256,
            )
        digest, turn_ref, terminal = current
        if digest != input_sha256:
            raise UniversalMayakDemoError("mayak_demo_replay_conflict")
        return DemoTurnReservation(
            (
                DemoReservationState.TERMINAL
                if terminal is not None
                else DemoReservationState.RUNNING
            ),
            turn_ref,
            digest,
            terminal,
        )

    async def load_context(self, scope, *, observed_at):
        return self.context_by_dialog.get(scope.dialog_ref, ())

    async def stage_reply(
        self,
        *,
        turn_ref,
        scope,
        candidate_text,
        candidate_sha256,
        now,
    ):
        delivery_ref = f"delivery_{turn_ref}"
        self.staged[delivery_ref] = candidate_text
        return delivery_ref

    async def finish_turn(self, *, turn_ref, terminal, now):
        for key, (digest, ref, _old) in self.turns.items():
            if ref == turn_ref:
                self.turns[key] = (digest, ref, terminal)
                return
        raise AssertionError("unknown turn")

    async def transition_installation(self, *, scope, target, now):
        self.transitions.append(target)
        self.installation = replace(self.installation, state=target)


class Model:
    def __init__(self):
        self.calls = []

    async def propose(self, request):
        self.calls.append(request)
        return UniversalMayakModelReply("Чем я могу помочь?")


class Delivery:
    def __init__(self, state=DemoDeliveryState.SENT):
        self.state = state
        self.calls: list[str] = []

    async def deliver(self, delivery_ref):
        self.calls.append(delivery_ref)
        if self.state is DemoDeliveryState.SENT:
            return DemoDeliveryReceipt(
                delivery_ref,
                self.state,
                provider_message_ref="provider_message_1",
            )
        return DemoDeliveryReceipt(
            delivery_ref,
            self.state,
            error_code=f"synthetic_{self.state.value}",
        )


class RaisingDelivery(Delivery):
    async def deliver(self, delivery_ref):
        self.calls.append(delivery_ref)
        raise RuntimeError("synthetic delivery timeout")


class MismatchedDelivery(Delivery):
    async def deliver(self, delivery_ref):
        self.calls.append(delivery_ref)
        return DemoDeliveryReceipt(
            "delivery_wrong",
            DemoDeliveryState.SENT,
            provider_message_ref="provider_message_1",
        )


def command(
    *,
    scope=SCOPE,
    key="idem_1",
    event="event_1",
    invocation=DemoInvocation.START,
    text="/start",
    actor="owner",
):
    return ProcessUniversalMayakCommand(
        scope,
        event,
        key,
        invocation,
        text,
        actor,
        NOW,
    )


def service(store=None, model=None, delivery=None):
    resolved_store = store or Store()
    resolved_model = model or Model()
    resolved_delivery = delivery or Delivery()
    return (
        ProcessUniversalMayakDemoService(
            store=resolved_store,
            model=resolved_model,
            delivery=resolved_delivery,
        ),
        resolved_store,
        resolved_model,
        resolved_delivery,
    )


@pytest.mark.anyio
async def test_start_uses_one_prompt_and_one_opaque_delivery() -> None:
    app, store, model, delivery = service()

    result = await app.execute(command())

    assert result.disposition is DemoDisposition.SENT
    assert result.replay is False
    assert len(model.calls) == 1
    assert model.calls[0].prompt_ref == "universal-mayak-v1"
    assert model.calls[0].prompt_sha256 == (
        "84937bfe6638429babdf8c10946f1eeb7896be3e20c03b82d7149593b9330eb2"
    )
    assert model.calls[0].user_text == "/start"
    assert delivery.calls == ["delivery_turn_1"]
    assert store.staged["delivery_turn_1"] == (
        "Маяк · цифровой помощник\n\nЧем я могу помочь?"
    )


def test_universal_prompt_is_exact_and_has_no_external_authority() -> None:
    prompt = (
        Path(__file__).parents[1]
        / "agent_bundles"
        / "universal_mayak_v1"
        / "system.md"
    ).read_bytes()

    assert sha256(prompt).hexdigest() == (
        "84937bfe6638429babdf8c10946f1eeb7896be3e20c03b82d7149593b9330eb2"
    )
    decoded = prompt.decode("utf-8")
    assert "Ты — Маяк" in decoded
    assert "без классификации" in decoded
    assert "Gate отдельно связывает" in decoded
    assert "Команды паузы и передачи человеку обрабатывает Gate" in decoded


@pytest.mark.anyio
async def test_exact_replay_never_repeats_model_or_delivery() -> None:
    app, _store, model, delivery = service()
    first = await app.execute(command())
    replay = await app.execute(command())

    assert first.disposition is DemoDisposition.SENT
    assert replay.disposition is DemoDisposition.REPLAYED
    assert replay.replay is True
    assert replay.delivery_ref == first.delivery_ref
    assert len(model.calls) == 1
    assert delivery.calls == ["delivery_turn_1"]


@pytest.mark.anyio
async def test_changed_payload_under_same_key_fails_closed() -> None:
    app, _store, model, delivery = service()
    await app.execute(command())

    with pytest.raises(UniversalMayakDemoError, match="replay_conflict"):
        await app.execute(
            command(
                invocation=DemoInvocation.DIRECT,
                text="Изменённый запрос",
            )
        )

    assert len(model.calls) == 1
    assert len(delivery.calls) == 1


@pytest.mark.anyio
async def test_continuation_receives_only_same_dialog_bounded_context() -> None:
    store = Store()
    store.context_by_dialog[SCOPE.dialog_ref] = (
        DemoContextMessage(
            SCOPE,
            "message_1",
            "assistant",
            "Предыдущий ответ",
            NOW - timedelta(minutes=1),
        ),
    )
    app, _store, model, _delivery = service(store=store)

    await app.execute(
        command(
            invocation=DemoInvocation.DIRECT,
            text="Продолжим",
        )
    )

    assert [item.text for item in model.calls[0].context] == ["Предыдущий ответ"]


@pytest.mark.anyio
async def test_cross_dialog_context_is_rejected_before_model() -> None:
    store = Store()
    other = DemoScope("workspace_owner", "installation_demo", "dialog_other")
    store.context_by_dialog[SCOPE.dialog_ref] = (
        DemoContextMessage(
            other,
            "message_other",
            "user",
            "Чужой контекст",
            NOW,
        ),
    )
    app, _store, model, delivery = service(store=store)

    with pytest.raises(UniversalMayakDemoError, match="context_scope_mismatch"):
        await app.execute(command())

    assert model.calls == []
    assert delivery.calls == []


@pytest.mark.anyio
async def test_stale_or_oversized_context_is_rejected_before_model() -> None:
    store = Store()
    store.context_by_dialog[SCOPE.dialog_ref] = (
        DemoContextMessage(
            SCOPE,
            "message_stale",
            "user",
            "Старый контекст",
            NOW - timedelta(days=2),
        ),
    )
    app, _store, model, delivery = service(store=store)

    with pytest.raises(UniversalMayakDemoError, match="retention_invalid"):
        await app.execute(command())

    assert model.calls == []
    assert delivery.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("installation", "actor", "code"),
    [
        (
            UniversalMayakInstallation(
                SCOPE,
                "Маяк",
                "цифровой помощник",
                state=DemoInstallationState.PAUSED,
            ),
            "owner",
            "installation_not_active",
        ),
        (
            UniversalMayakInstallation(
                SCOPE,
                "Маяк",
                "цифровой помощник",
                state=DemoInstallationState.ACTIVE,
            ),
            "invited_user",
            "owner_only",
        ),
        (
            UniversalMayakInstallation(
                SCOPE,
                "Маяк",
                "цифровой помощник",
                state=DemoInstallationState.ACTIVE,
                owner_only=False,
            ),
            "owner",
            "owner_only",
        ),
    ],
)
async def test_default_off_and_owner_only_block_before_effect(
    installation,
    actor,
    code,
) -> None:
    app, store, model, delivery = service(store=Store(installation))

    with pytest.raises(UniversalMayakDemoError, match=code):
        await app.execute(command(actor=actor))

    assert model.calls == []
    assert delivery.calls == []
    if code == "installation_not_active":
        assert next(iter(store.turns.values()))[2] == DemoTerminalReceipt(
            DemoDisposition.BLOCKED,
            "mayak_demo_installation_not_active",
        )
    else:
        assert store.turns == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("invocation", "text", "target", "disposition"),
    [
        (
            DemoInvocation.PAUSE,
            "/pause",
            DemoInstallationState.PAUSED,
            DemoDisposition.PAUSED,
        ),
        (
            DemoInvocation.HUMAN_TAKEOVER,
            "/human",
            DemoInstallationState.HUMAN_TAKEOVER,
            DemoDisposition.HUMAN_TAKEOVER,
        ),
    ],
)
async def test_pause_and_human_takeover_are_terminal_without_model_or_delivery(
    invocation,
    text,
    target,
    disposition,
) -> None:
    app, store, model, delivery = service()
    result = await app.execute(
        command(invocation=invocation, text=text)
    )

    assert result.disposition is disposition
    assert store.transitions == [target]
    assert model.calls == []
    assert delivery.calls == []


@pytest.mark.anyio
async def test_pause_exact_replay_returns_receipt_after_installation_is_paused() -> None:
    app, store, model, delivery = service()
    pause = command(invocation=DemoInvocation.PAUSE, text="/pause")

    first = await app.execute(pause)
    replay = await app.execute(pause)

    assert first.disposition is DemoDisposition.PAUSED
    assert replay.disposition is DemoDisposition.REPLAYED
    assert replay.code == "mayak_demo_paused"
    assert store.transitions == [DemoInstallationState.PAUSED]
    assert model.calls == []
    assert delivery.calls == []


@pytest.mark.anyio
async def test_unknown_delivery_is_terminal_and_exact_replay_does_not_retry() -> None:
    app, _store, model, delivery = service(
        delivery=Delivery(DemoDeliveryState.UNKNOWN)
    )

    first = await app.execute(command())
    replay = await app.execute(command())

    assert first.disposition is DemoDisposition.UNKNOWN
    assert replay.disposition is DemoDisposition.REPLAYED
    assert replay.code == "mayak_demo_delivery_unknown"
    assert len(model.calls) == 1
    assert delivery.calls == ["delivery_turn_1"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "delivery",
    [RaisingDelivery(), MismatchedDelivery()],
)
async def test_ambiguous_delivery_is_unknown_and_exact_replay_never_retries(
    delivery,
) -> None:
    app, _store, model, _delivery = service(delivery=delivery)

    first = await app.execute(command())
    replay = await app.execute(command())

    assert first.disposition is DemoDisposition.UNKNOWN
    assert first.code == "mayak_demo_delivery_unknown"
    assert replay.disposition is DemoDisposition.REPLAYED
    assert replay.code == "mayak_demo_delivery_unknown"
    assert len(model.calls) == 1
    assert delivery.calls == ["delivery_turn_1"]


def test_terminal_receipt_rejects_inconsistent_delivery_state() -> None:
    sent = DemoDeliveryReceipt(
        "delivery_1",
        DemoDeliveryState.SENT,
        provider_message_ref="provider_message_1",
    )

    with pytest.raises(ValueError, match="must match"):
        DemoTerminalReceipt(DemoDisposition.UNKNOWN, "unknown", sent)

    with pytest.raises(ValueError, match="result projection"):
        DemoTerminalReceipt(DemoDisposition.REPLAYED, "replayed")
