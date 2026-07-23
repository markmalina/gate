"""Pure orchestration for one owner-only universal Mayak private-dialog demo.

The module deliberately contains no Telegram, OpenAI, database, credential, or
configuration client. Production adapters must reuse Gate's existing model and
delivery boundaries. The application service sees only opaque references.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

UNIVERSAL_MAYAK_PROMPT_REF = "universal-mayak-v1"
UNIVERSAL_MAYAK_PROMPT_SHA256 = (
    "84937bfe6638429babdf8c10946f1eeb7896be3e20c03b82d7149593b9330eb2"
)
MAX_USER_TEXT_CHARS = 4_000
MAX_REPLY_TEXT_CHARS = 4_096
MAX_CONTEXT_MESSAGES = 12
MAX_CONTEXT_CHARS = 6_000
MAX_RETENTION_SECONDS = 86_400


class UniversalMayakDemoError(RuntimeError):
    """A bounded refusal that contains no message or provider payload."""

    def __init__(self, code: str) -> None:
        self.code = code.strip()[:128] or "mayak_demo_error"
        super().__init__(self.code)


class DemoInstallationState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    HUMAN_TAKEOVER = "human_takeover"


class DemoInvocation(StrEnum):
    START = "start"
    DIRECT = "direct"
    PAUSE = "pause"
    HUMAN_TAKEOVER = "human_takeover"


class DemoReservationState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    TERMINAL = "terminal"


class DemoDeliveryState(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class DemoDisposition(StrEnum):
    SENT = "sent"
    REPLAYED = "replayed"
    PAUSED = "paused"
    HUMAN_TAKEOVER = "human_takeover"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DemoScope:
    workspace_ref: str
    installation_ref: str
    dialog_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.workspace_ref, "workspace_ref", 128)
        _require_token(self.installation_ref, "installation_ref", 128)
        _require_token(self.dialog_ref, "dialog_ref", 128)


@dataclass(frozen=True, slots=True)
class UniversalMayakInstallation:
    scope: DemoScope
    public_label: str
    disclosure_text: str
    state: DemoInstallationState = DemoInstallationState.PAUSED
    owner_only: bool = True
    dialog_kind: str = "private"
    context_message_limit: int = MAX_CONTEXT_MESSAGES
    context_char_limit: int = MAX_CONTEXT_CHARS
    retention_seconds: int = MAX_RETENTION_SECONDS

    def __post_init__(self) -> None:
        _require_text(self.public_label, "public_label", 80)
        _require_text(self.disclosure_text, "disclosure_text", 240)
        if self.dialog_kind != "private":
            raise ValueError("universal Mayak demo requires a private dialog")
        if not 1 <= self.context_message_limit <= MAX_CONTEXT_MESSAGES:
            raise ValueError("context_message_limit is outside the demo bound")
        if not 1 <= self.context_char_limit <= MAX_CONTEXT_CHARS:
            raise ValueError("context_char_limit is outside the demo bound")
        if not 1 <= self.retention_seconds <= MAX_RETENTION_SECONDS:
            raise ValueError("retention_seconds is outside the demo bound")


@dataclass(frozen=True, slots=True)
class DemoContextMessage:
    scope: DemoScope
    message_ref: str = field(repr=False)
    role: str
    text: str = field(repr=False)
    occurred_at: datetime

    def __post_init__(self) -> None:
        _require_token(self.message_ref, "message_ref", 128)
        if self.role not in {"user", "assistant"}:
            raise ValueError("context role must be user or assistant")
        _require_text(self.text, "context text", 2_000)
        _require_aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class ProcessUniversalMayakCommand:
    scope: DemoScope
    event_ref: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    invocation: DemoInvocation
    text: str = field(repr=False)
    actor_scope: str
    now: datetime

    def __post_init__(self) -> None:
        _require_token(self.event_ref, "event_ref", 128)
        _require_token(self.idempotency_key, "idempotency_key", 128)
        if self.invocation is DemoInvocation.START:
            if self.text.strip() != "/start":
                raise ValueError("start invocation requires exact /start")
        elif self.invocation is DemoInvocation.PAUSE:
            if self.text.strip() != "/pause":
                raise ValueError("pause invocation requires exact /pause")
        elif self.invocation is DemoInvocation.HUMAN_TAKEOVER:
            if self.text.strip() != "/human":
                raise ValueError("human takeover requires exact /human")
        else:
            _require_text(self.text, "text", MAX_USER_TEXT_CHARS)
        _require_token(self.actor_scope, "actor_scope", 64)
        _require_aware(self.now, "now")


@dataclass(frozen=True, slots=True)
class UniversalMayakModelRequest:
    prompt_ref: str
    prompt_sha256: str
    public_label: str
    user_text: str = field(repr=False)
    context: tuple[DemoContextMessage, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class UniversalMayakModelReply:
    text: str = field(repr=False)
    result_code: str = "mayak_reply"

    def __post_init__(self) -> None:
        _require_text(self.text, "model reply", MAX_REPLY_TEXT_CHARS)
        _require_token(self.result_code, "result_code", 128)


@dataclass(frozen=True, slots=True)
class DemoDeliveryReceipt:
    delivery_ref: str
    state: DemoDeliveryState
    provider_message_ref: str | None = field(default=None, repr=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        _require_token(self.delivery_ref, "delivery_ref", 128)
        if self.state is DemoDeliveryState.SENT:
            if self.provider_message_ref is None or self.error_code is not None:
                raise ValueError("sent receipt requires only a provider message reference")
            _require_token(self.provider_message_ref, "provider_message_ref", 255)
        else:
            if self.provider_message_ref is not None or self.error_code is None:
                raise ValueError("non-sent receipt requires only an error code")
            _require_token(self.error_code, "error_code", 128)


@dataclass(frozen=True, slots=True)
class DemoTerminalReceipt:
    disposition: DemoDisposition
    code: str
    delivery: DemoDeliveryReceipt | None = None

    def __post_init__(self) -> None:
        _require_token(self.code, "code", 128)
        if self.disposition is DemoDisposition.SENT:
            if self.delivery is None or self.delivery.state is not DemoDeliveryState.SENT:
                raise ValueError("sent terminal receipt requires sent delivery")
        if self.disposition in {
            DemoDisposition.PAUSED,
            DemoDisposition.HUMAN_TAKEOVER,
        } and self.delivery is not None:
            raise ValueError("control transition cannot contain a delivery receipt")


@dataclass(frozen=True, slots=True)
class DemoTurnReservation:
    state: DemoReservationState
    turn_ref: str
    input_sha256: str = field(repr=False)
    terminal: DemoTerminalReceipt | None = None

    def __post_init__(self) -> None:
        _require_token(self.turn_ref, "turn_ref", 128)
        if len(self.input_sha256) != 64:
            raise ValueError("input_sha256 must be a SHA-256 digest")
        if (self.state is DemoReservationState.TERMINAL) != (
            self.terminal is not None
        ):
            raise ValueError("only a terminal reservation carries a terminal receipt")


@dataclass(frozen=True, slots=True)
class UniversalMayakDemoResult:
    disposition: DemoDisposition
    code: str
    turn_ref: str
    delivery_ref: str | None = None
    replay: bool = False


class UniversalMayakDemoStore(Protocol):
    """Durable, workspace-scoped phases around model and delivery calls."""

    async def resolve_installation(
        self, scope: DemoScope
    ) -> UniversalMayakInstallation: ...

    async def reserve_turn(
        self,
        command: ProcessUniversalMayakCommand,
        *,
        input_sha256: str,
    ) -> DemoTurnReservation:
        """Reserve once; changed payload under one key must raise a replay conflict."""

    async def load_context(
        self,
        scope: DemoScope,
        *,
        observed_at: datetime,
    ) -> tuple[DemoContextMessage, ...]: ...

    async def stage_reply(
        self,
        *,
        turn_ref: str,
        scope: DemoScope,
        candidate_text: str,
        candidate_sha256: str,
        now: datetime,
    ) -> str:
        """Commit exact text and opaque target binding before external delivery."""

    async def finish_turn(
        self,
        *,
        turn_ref: str,
        terminal: DemoTerminalReceipt,
        now: datetime,
    ) -> None: ...

    async def transition_installation(
        self,
        *,
        scope: DemoScope,
        target: DemoInstallationState,
        now: datetime,
    ) -> None: ...


class UniversalMayakModelPort(Protocol):
    """Adapter target for Gate's existing AgentRuntimePort/model runtime."""

    async def propose(
        self, request: UniversalMayakModelRequest
    ) -> UniversalMayakModelReply: ...


class UniversalMayakDeliveryPort(Protocol):
    """Adapter target for Gate's existing durable Telegram delivery path.

    The port receives only an opaque delivery reference. Target and exact text
    remain bound in the durable Gate store and are never selected by the model.
    """

    async def deliver(self, delivery_ref: str) -> DemoDeliveryReceipt: ...


class ProcessUniversalMayakDemoService:
    """Run one private-dialog turn with exact replay and no implicit retries."""

    def __init__(
        self,
        *,
        store: UniversalMayakDemoStore,
        model: UniversalMayakModelPort,
        delivery: UniversalMayakDeliveryPort,
    ) -> None:
        self._store = store
        self._model = model
        self._delivery = delivery

    async def execute(
        self, command: ProcessUniversalMayakCommand
    ) -> UniversalMayakDemoResult:
        installation = await self._store.resolve_installation(command.scope)
        _admit(installation, command)
        input_sha256 = _input_digest(command)
        reservation = await self._store.reserve_turn(
            command,
            input_sha256=input_sha256,
        )
        if reservation.input_sha256 != input_sha256:
            raise UniversalMayakDemoError("mayak_demo_replay_conflict")
        if reservation.state is DemoReservationState.TERMINAL:
            assert reservation.terminal is not None
            return _terminal_result(
                reservation.turn_ref,
                reservation.terminal,
                replay=True,
            )
        if reservation.state is DemoReservationState.RUNNING:
            return UniversalMayakDemoResult(
                DemoDisposition.BLOCKED,
                "mayak_demo_turn_in_progress",
                reservation.turn_ref,
            )

        if command.invocation in {
            DemoInvocation.PAUSE,
            DemoInvocation.HUMAN_TAKEOVER,
        }:
            target = (
                DemoInstallationState.PAUSED
                if command.invocation is DemoInvocation.PAUSE
                else DemoInstallationState.HUMAN_TAKEOVER
            )
            disposition = (
                DemoDisposition.PAUSED
                if target is DemoInstallationState.PAUSED
                else DemoDisposition.HUMAN_TAKEOVER
            )
            terminal = DemoTerminalReceipt(disposition, f"mayak_demo_{target.value}")
            await self._store.transition_installation(
                scope=command.scope,
                target=target,
                now=command.now,
            )
            await self._store.finish_turn(
                turn_ref=reservation.turn_ref,
                terminal=terminal,
                now=command.now,
            )
            return _terminal_result(reservation.turn_ref, terminal)

        context = await self._store.load_context(
            command.scope,
            observed_at=command.now,
        )
        _validate_context(installation, context, observed_at=command.now)
        try:
            model_reply = await self._model.propose(
                UniversalMayakModelRequest(
                    prompt_ref=UNIVERSAL_MAYAK_PROMPT_REF,
                    prompt_sha256=UNIVERSAL_MAYAK_PROMPT_SHA256,
                    public_label=installation.public_label,
                    user_text=command.text,
                    context=context,
                )
            )
            candidate = _visible_reply(installation, model_reply.text)
        except Exception:
            terminal = DemoTerminalReceipt(
                DemoDisposition.FAILED,
                "mayak_demo_model_failed",
            )
            await self._store.finish_turn(
                turn_ref=reservation.turn_ref,
                terminal=terminal,
                now=command.now,
            )
            return _terminal_result(reservation.turn_ref, terminal)

        candidate_sha256 = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        delivery_ref = await self._store.stage_reply(
            turn_ref=reservation.turn_ref,
            scope=command.scope,
            candidate_text=candidate,
            candidate_sha256=candidate_sha256,
            now=command.now,
        )
        delivery = await self._delivery.deliver(delivery_ref)
        if delivery.delivery_ref != delivery_ref:
            raise UniversalMayakDemoError("mayak_demo_delivery_ref_mismatch")
        terminal = _delivery_terminal(delivery)
        await self._store.finish_turn(
            turn_ref=reservation.turn_ref,
            terminal=terminal,
            now=command.now,
        )
        return _terminal_result(reservation.turn_ref, terminal)


def _admit(
    installation: UniversalMayakInstallation,
    command: ProcessUniversalMayakCommand,
) -> None:
    if installation.scope != command.scope:
        raise UniversalMayakDemoError("mayak_demo_scope_mismatch")
    if installation.dialog_kind != "private":
        raise UniversalMayakDemoError("mayak_demo_dialog_not_private")
    if not installation.owner_only or command.actor_scope != "owner":
        raise UniversalMayakDemoError("mayak_demo_owner_only")
    if installation.state is not DemoInstallationState.ACTIVE:
        raise UniversalMayakDemoError("mayak_demo_installation_not_active")


def _validate_context(
    installation: UniversalMayakInstallation,
    context: tuple[DemoContextMessage, ...],
    *,
    observed_at: datetime,
) -> None:
    if len(context) > installation.context_message_limit:
        raise UniversalMayakDemoError("mayak_demo_context_message_limit")
    if sum(len(item.text) for item in context) > installation.context_char_limit:
        raise UniversalMayakDemoError("mayak_demo_context_char_limit")
    cutoff = observed_at - timedelta(seconds=installation.retention_seconds)
    message_refs: set[str] = set()
    for item in context:
        if item.scope != installation.scope:
            raise UniversalMayakDemoError("mayak_demo_context_scope_mismatch")
        if item.occurred_at < cutoff or item.occurred_at > observed_at:
            raise UniversalMayakDemoError("mayak_demo_context_retention_invalid")
        if item.message_ref in message_refs:
            raise UniversalMayakDemoError("mayak_demo_context_duplicate")
        message_refs.add(item.message_ref)


def _visible_reply(
    installation: UniversalMayakInstallation,
    model_text: str,
) -> str:
    prefix = f"{installation.public_label} · {installation.disclosure_text}"
    candidate = f"{prefix}\n\n{model_text.strip()}"
    if len(candidate) > MAX_REPLY_TEXT_CHARS:
        raise UniversalMayakDemoError("mayak_demo_reply_too_long")
    return candidate


def _input_digest(command: ProcessUniversalMayakCommand) -> str:
    digest = hashlib.sha256()
    for value in (
        command.scope.workspace_ref,
        command.scope.installation_ref,
        command.scope.dialog_ref,
        command.event_ref,
        command.invocation.value,
        command.text,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _delivery_terminal(delivery: DemoDeliveryReceipt) -> DemoTerminalReceipt:
    if delivery.state is DemoDeliveryState.SENT:
        return DemoTerminalReceipt(
            DemoDisposition.SENT,
            "mayak_demo_sent",
            delivery,
        )
    disposition = {
        DemoDeliveryState.FAILED: DemoDisposition.FAILED,
        DemoDeliveryState.UNKNOWN: DemoDisposition.UNKNOWN,
        DemoDeliveryState.BLOCKED: DemoDisposition.BLOCKED,
    }[delivery.state]
    return DemoTerminalReceipt(
        disposition,
        f"mayak_demo_delivery_{delivery.state.value}",
        delivery,
    )


def _terminal_result(
    turn_ref: str,
    terminal: DemoTerminalReceipt,
    *,
    replay: bool = False,
) -> UniversalMayakDemoResult:
    return UniversalMayakDemoResult(
        DemoDisposition.REPLAYED if replay else terminal.disposition,
        terminal.code,
        turn_ref,
        (
            terminal.delivery.delivery_ref
            if terminal.delivery is not None
            else None
        ),
        replay,
    )


def _require_token(value: str, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded token")


def _require_text(value: str, field_name: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be bounded non-empty text")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
