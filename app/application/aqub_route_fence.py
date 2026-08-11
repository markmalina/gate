"""Disabled multi-installation route journal for the AQUB transport boundary.

This module is deliberately pure and is not imported by Gate's runtime.  It
models the route-generation and single-writer rules needed to rehearse adding
another Telegram bot installation without changing webhooks, credentials,
database state, provider traffic, or the currently active bot.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

_REFERENCE_SUFFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._~-]{7,127}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:~/-]{7,255}$")


class AQUBRouteFenceError(RuntimeError):
    """A fail-closed refusal from the AQUB multi-installation route fence."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AQUBRouteFenceMode(StrEnum):
    """The route journal has no production activation mode."""

    DISABLED = "disabled"
    SYNTHETIC_REHEARSAL = "synthetic_rehearsal"


class AQUBRouteJournalAction(StrEnum):
    """Append-only route transitions; every new binding begins on hold."""

    REGISTER_HOLD = "register_hold"
    ACTIVATE = "activate"
    HOLD = "hold"
    RETIRE = "retire"


class AQUBRouteStatus(StrEnum):
    HOLD = "hold"
    ACTIVE = "active"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class AQUBRouteBinding:
    """Opaque installation-to-Cube route for exactly one conversation."""

    binding_ref: str
    installation_ref: str
    conversation_ref: str
    cube_ref: str
    route_generation: int

    def __post_init__(self) -> None:
        _assert_opaque_ref(self.binding_ref, "binding")
        _assert_opaque_ref(self.installation_ref, "installation")
        _assert_opaque_ref(self.conversation_ref, "conversation")
        _assert_opaque_ref(self.cube_ref, "cube")
        if (
            type(self.route_generation) is not int
            or self.route_generation < 1
            or self.route_generation > 2_147_483_647
        ):
            raise AQUBRouteFenceError("AQUB_ROUTE_GENERATION_INVALID")


@dataclass(frozen=True, slots=True)
class AQUBRouteJournalEntry:
    """One exact, replayable transition in the route journal."""

    sequence: int
    action: AQUBRouteJournalAction
    binding: AQUBRouteBinding

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise AQUBRouteFenceError("AQUB_ROUTE_JOURNAL_SEQUENCE_INVALID")
        if type(self.action) is not AQUBRouteJournalAction:
            raise AQUBRouteFenceError("AQUB_ROUTE_JOURNAL_ACTION_INVALID")
        if type(self.binding) is not AQUBRouteBinding:
            raise AQUBRouteFenceError("AQUB_ROUTE_BINDING_INVALID")


@dataclass(frozen=True, slots=True)
class AQUBRouteState:
    binding: AQUBRouteBinding
    status: AQUBRouteStatus


@dataclass(frozen=True, slots=True)
class AQUBRouteSnapshot:
    """Immutable result of replaying a complete synthetic route journal."""

    routes: tuple[AQUBRouteState, ...]
    last_sequence: int

    def active_route(self, conversation_ref: str) -> AQUBRouteBinding | None:
        _assert_opaque_ref(conversation_ref, "conversation")
        matches = tuple(
            route.binding
            for route in self.routes
            if route.status is AQUBRouteStatus.ACTIVE
            and route.binding.conversation_ref == conversation_ref
        )
        if len(matches) > 1:
            raise AQUBRouteFenceError("AQUB_ROUTE_MULTIPLE_ACTIVE_WRITERS")
        return matches[0] if matches else None

    def require_active(self, expected: AQUBRouteBinding) -> AQUBRouteBinding:
        """Fail closed unless all route-generation fields match the active writer."""

        if type(expected) is not AQUBRouteBinding:
            raise AQUBRouteFenceError("AQUB_ROUTE_BINDING_INVALID")
        active = self.active_route(expected.conversation_ref)
        if active is None:
            raise AQUBRouteFenceError("AQUB_ROUTE_NOT_ACTIVE")
        if active.installation_ref != expected.installation_ref:
            raise AQUBRouteFenceError("AQUB_ROUTE_INSTALLATION_MISMATCH")
        if active.cube_ref != expected.cube_ref:
            raise AQUBRouteFenceError("AQUB_ROUTE_CUBE_MISMATCH")
        if active.route_generation != expected.route_generation:
            raise AQUBRouteFenceError("AQUB_ROUTE_GENERATION_STALE")
        if active.binding_ref != expected.binding_ref:
            raise AQUBRouteFenceError("AQUB_ROUTE_BINDING_MISMATCH")
        return active


@dataclass(frozen=True, slots=True)
class AQUBRouteFence:
    """Production-inactive seam for synthetic multi-installation rehearsals."""

    mode: AQUBRouteFenceMode = AQUBRouteFenceMode.DISABLED

    def __post_init__(self) -> None:
        if type(self.mode) is not AQUBRouteFenceMode:
            raise AQUBRouteFenceError("AQUB_ROUTE_FENCE_MODE_INVALID")

    def rehearse(
        self, entries: tuple[AQUBRouteJournalEntry, ...]
    ) -> AQUBRouteSnapshot:
        if self.mode is not AQUBRouteFenceMode.SYNTHETIC_REHEARSAL:
            raise AQUBRouteFenceError("AQUB_ROUTE_FENCE_DISABLED")
        if type(entries) is not tuple:
            raise AQUBRouteFenceError("AQUB_ROUTE_JOURNAL_INVALID")
        return replay_route_journal(entries)


def replay_route_journal(
    entries: tuple[AQUBRouteJournalEntry, ...],
) -> AQUBRouteSnapshot:
    """Replay a complete journal and enforce generation and writer fences."""

    if type(entries) is not tuple:
        raise AQUBRouteFenceError("AQUB_ROUTE_JOURNAL_INVALID")

    states: dict[str, AQUBRouteState] = {}
    expected_sequence = 1
    for entry in entries:
        if type(entry) is not AQUBRouteJournalEntry:
            raise AQUBRouteFenceError("AQUB_ROUTE_JOURNAL_ENTRY_INVALID")
        if entry.sequence != expected_sequence:
            raise AQUBRouteFenceError("AQUB_ROUTE_JOURNAL_SEQUENCE_GAP")
        expected_sequence += 1

        binding = entry.binding
        existing = states.get(binding.binding_ref)
        if entry.action is AQUBRouteJournalAction.REGISTER_HOLD:
            if existing is not None:
                raise AQUBRouteFenceError("AQUB_ROUTE_BINDING_ALREADY_REGISTERED")
            _assert_new_binding_scope(states, binding)
            states[binding.binding_ref] = AQUBRouteState(
                binding=binding,
                status=AQUBRouteStatus.HOLD,
            )
            continue

        if existing is None:
            raise AQUBRouteFenceError("AQUB_ROUTE_BINDING_NOT_REGISTERED")
        if existing.binding != binding:
            raise AQUBRouteFenceError("AQUB_ROUTE_BINDING_EVIDENCE_MISMATCH")

        if entry.action is AQUBRouteJournalAction.ACTIVATE:
            if existing.status is not AQUBRouteStatus.HOLD:
                raise AQUBRouteFenceError("AQUB_ROUTE_ACTIVATION_INVALID")
            if any(
                state.status is AQUBRouteStatus.ACTIVE
                and state.binding.conversation_ref == binding.conversation_ref
                for state in states.values()
            ):
                raise AQUBRouteFenceError("AQUB_ROUTE_MULTIPLE_ACTIVE_WRITERS")
            states[binding.binding_ref] = AQUBRouteState(
                binding=binding,
                status=AQUBRouteStatus.ACTIVE,
            )
        elif entry.action is AQUBRouteJournalAction.HOLD:
            if existing.status is not AQUBRouteStatus.ACTIVE:
                raise AQUBRouteFenceError("AQUB_ROUTE_HOLD_INVALID")
            states[binding.binding_ref] = AQUBRouteState(
                binding=binding,
                status=AQUBRouteStatus.HOLD,
            )
        elif entry.action is AQUBRouteJournalAction.RETIRE:
            if existing.status is not AQUBRouteStatus.HOLD:
                raise AQUBRouteFenceError("AQUB_ROUTE_RETIRE_INVALID")
            states[binding.binding_ref] = AQUBRouteState(
                binding=binding,
                status=AQUBRouteStatus.RETIRED,
            )

    ordered = tuple(states[key] for key in sorted(states))
    return AQUBRouteSnapshot(routes=ordered, last_sequence=len(entries))


def installation_scoped_idempotency_key(
    installation_ref: str, idempotency_key: str
) -> str:
    """Derive a stable key whose collision domain includes the bot installation."""

    _assert_opaque_ref(installation_ref, "installation")
    if (
        not isinstance(idempotency_key, str)
        or _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key) is None
    ):
        raise AQUBRouteFenceError("AQUB_ROUTE_IDEMPOTENCY_KEY_INVALID")
    digest = hashlib.sha256(
        f"{installation_ref}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"gate-aqub-v2/{digest}"


def _assert_new_binding_scope(
    states: dict[str, AQUBRouteState], binding: AQUBRouteBinding
) -> None:
    same_conversation = tuple(
        state for state in states.values()
        if state.binding.conversation_ref == binding.conversation_ref
    )
    if same_conversation and binding.route_generation <= max(
        state.binding.route_generation for state in same_conversation
    ):
        raise AQUBRouteFenceError("AQUB_ROUTE_GENERATION_STALE")
    if any(
        state.status is not AQUBRouteStatus.RETIRED
        and state.binding.installation_ref == binding.installation_ref
        and state.binding.conversation_ref == binding.conversation_ref
        for state in states.values()
    ):
        raise AQUBRouteFenceError("AQUB_ROUTE_INSTALLATION_ALREADY_BOUND")


def _assert_opaque_ref(value: object, prefix: str) -> str:
    if not isinstance(value, str):
        raise AQUBRouteFenceError("AQUB_ROUTE_REFERENCE_INVALID")
    expected_prefix = f"{prefix}:"
    if not value.startswith(expected_prefix):
        raise AQUBRouteFenceError("AQUB_ROUTE_REFERENCE_INVALID")
    suffix = value[len(expected_prefix) :]
    if _REFERENCE_SUFFIX_RE.fullmatch(suffix) is None:
        raise AQUBRouteFenceError("AQUB_ROUTE_REFERENCE_INVALID")
    return value
