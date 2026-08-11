"""Synthetic proofs for the disabled AQUB multi-installation route fence."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.application.aqub_route_fence import (
    AQUBRouteBinding,
    AQUBRouteFence,
    AQUBRouteFenceError,
    AQUBRouteFenceMode,
    AQUBRouteJournalAction,
    AQUBRouteJournalEntry,
    AQUBRouteStatus,
    installation_scoped_idempotency_key,
)


def _binding(
    *,
    name: str,
    installation: str,
    conversation: str,
    generation: int,
) -> AQUBRouteBinding:
    return AQUBRouteBinding(
        binding_ref=f"binding:{name}_binding_01",
        installation_ref=f"installation:{installation}_install_01",
        conversation_ref=f"conversation:{conversation}_group_01",
        cube_ref="cube:primary_cube_01",
        route_generation=generation,
    )


def _entry(
    sequence: int,
    action: AQUBRouteJournalAction,
    binding: AQUBRouteBinding,
) -> AQUBRouteJournalEntry:
    return AQUBRouteJournalEntry(
        sequence=sequence,
        action=action,
        binding=binding,
    )


def _fence() -> AQUBRouteFence:
    return AQUBRouteFence(mode=AQUBRouteFenceMode.SYNTHETIC_REHEARSAL)


def test_route_fence_is_disabled_by_default_and_rejects_string_activation() -> None:
    with pytest.raises(AQUBRouteFenceError, match="FENCE_DISABLED"):
        AQUBRouteFence().rehearse(())
    with pytest.raises(AQUBRouteFenceError, match="MODE_INVALID"):
        AQUBRouteFence(mode="synthetic_rehearsal")  # type: ignore[arg-type]


def test_old_bot_and_aqubovich_can_write_to_different_groups() -> None:
    old_bot = _binding(
        name="old_bot",
        installation="old_bot",
        conversation="wedding",
        generation=1,
    )
    aqubovich = _binding(
        name="aqubovich",
        installation="aqubovich",
        conversation="nutrition",
        generation=1,
    )
    snapshot = _fence().rehearse(
        (
            _entry(1, AQUBRouteJournalAction.REGISTER_HOLD, old_bot),
            _entry(2, AQUBRouteJournalAction.ACTIVATE, old_bot),
            _entry(3, AQUBRouteJournalAction.REGISTER_HOLD, aqubovich),
            _entry(4, AQUBRouteJournalAction.ACTIVATE, aqubovich),
        )
    )

    assert snapshot.require_active(old_bot) == old_bot
    assert snapshot.require_active(aqubovich) == aqubovich
    assert snapshot.last_sequence == 4


def test_same_group_switch_requires_old_bot_hold_before_aqubovich_activation() -> None:
    old_bot = _binding(
        name="old_bot",
        installation="old_bot",
        conversation="wedding",
        generation=1,
    )
    aqubovich = _binding(
        name="aqubovich",
        installation="aqubovich",
        conversation="wedding",
        generation=2,
    )
    conflicting = (
        _entry(1, AQUBRouteJournalAction.REGISTER_HOLD, old_bot),
        _entry(2, AQUBRouteJournalAction.ACTIVATE, old_bot),
        _entry(3, AQUBRouteJournalAction.REGISTER_HOLD, aqubovich),
        _entry(4, AQUBRouteJournalAction.ACTIVATE, aqubovich),
    )
    with pytest.raises(AQUBRouteFenceError, match="MULTIPLE_ACTIVE_WRITERS"):
        _fence().rehearse(conflicting)

    switched = _fence().rehearse(
        (
            *conflicting[:3],
            _entry(4, AQUBRouteJournalAction.HOLD, old_bot),
            _entry(5, AQUBRouteJournalAction.ACTIVATE, aqubovich),
        )
    )
    assert switched.require_active(aqubovich) == aqubovich
    assert switched.active_route(old_bot.conversation_ref) == aqubovich
    old_state = next(
        route for route in switched.routes if route.binding == old_bot
    )
    assert old_state.status is AQUBRouteStatus.HOLD


def test_stale_generation_and_route_mismatches_fail_closed() -> None:
    old_bot = _binding(
        name="old_bot",
        installation="old_bot",
        conversation="wedding",
        generation=1,
    )
    snapshot = _fence().rehearse(
        (
            _entry(1, AQUBRouteJournalAction.REGISTER_HOLD, old_bot),
            _entry(2, AQUBRouteJournalAction.ACTIVATE, old_bot),
        )
    )

    mismatch_cases = (
        (replace(old_bot, installation_ref="installation:other_bot_01"), "INSTALLATION"),
        (replace(old_bot, cube_ref="cube:other_cube_01"), "CUBE"),
        (replace(old_bot, route_generation=2), "GENERATION_STALE"),
        (replace(old_bot, binding_ref="binding:other_binding_01"), "BINDING_MISMATCH"),
    )
    for candidate, code in mismatch_cases:
        with pytest.raises(AQUBRouteFenceError, match=code):
            snapshot.require_active(candidate)

    stale = replace(
        old_bot,
        binding_ref="binding:stale_binding_01",
        installation_ref="installation:aqubovich_install_01",
    )
    with pytest.raises(AQUBRouteFenceError, match="GENERATION_STALE"):
        _fence().rehearse(
            (
                _entry(1, AQUBRouteJournalAction.REGISTER_HOLD, old_bot),
                _entry(2, AQUBRouteJournalAction.REGISTER_HOLD, stale),
            )
        )


def test_journal_gaps_and_invalid_transitions_fail_closed() -> None:
    old_bot = _binding(
        name="old_bot",
        installation="old_bot",
        conversation="wedding",
        generation=1,
    )
    with pytest.raises(AQUBRouteFenceError, match="SEQUENCE_GAP"):
        _fence().rehearse(
            (_entry(2, AQUBRouteJournalAction.REGISTER_HOLD, old_bot),)
        )
    with pytest.raises(AQUBRouteFenceError, match="RETIRE_INVALID"):
        _fence().rehearse(
            (
                _entry(1, AQUBRouteJournalAction.REGISTER_HOLD, old_bot),
                _entry(2, AQUBRouteJournalAction.ACTIVATE, old_bot),
                _entry(3, AQUBRouteJournalAction.RETIRE, old_bot),
            )
        )
    with pytest.raises(AQUBRouteFenceError, match="EVIDENCE_MISMATCH"):
        _fence().rehearse(
            (
                _entry(1, AQUBRouteJournalAction.REGISTER_HOLD, old_bot),
                _entry(
                    2,
                    AQUBRouteJournalAction.ACTIVATE,
                    replace(old_bot, cube_ref="cube:other_cube_01"),
                ),
            )
        )


def test_identical_provider_idempotency_values_do_not_collide_across_bots() -> None:
    shared = "delivery/shared/update/9001"
    old_key = installation_scoped_idempotency_key(
        "installation:old_bot_install_01", shared
    )
    aqubovich_key = installation_scoped_idempotency_key(
        "installation:aqubovich_install_01", shared
    )

    assert old_key != aqubovich_key
    assert old_key == installation_scoped_idempotency_key(
        "installation:old_bot_install_01", shared
    )
    assert old_key.startswith("gate-aqub-v2/")
