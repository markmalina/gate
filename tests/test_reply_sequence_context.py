"""Approval eligibility and bounded recipient labels without provider or DB effects."""

from dataclasses import replace
from uuid import uuid4

import pytest
from app.infrastructure.reply_sequence_context import ApprovalContextState, context_approval_reason
from app.infrastructure.reply_sequence_recipient import safe_recipient_display


def ready_state():
    trigger = uuid4()
    return ApprovalContextState(uuid4(), trigger, uuid4(), 7, "healthy", False, trigger)


def test_unchanged_projected_visible_context_can_authorize_its_own_revision():
    state = ready_state()
    assert context_approval_reason(state, state, {str(state.trigger_message_id)}) is None


@pytest.mark.parametrize(
    "change",
    [
        {"revision": 8},
        {"guard_id": uuid4()},
        {"trigger_message_id": uuid4()},
        {"pending": True},
        {"health": "blocked"},
        {"latest_message_id": None},
    ],
)
def test_any_changed_approval_state_cannot_upgrade_old_context(change):
    state = ready_state()
    assert (
        context_approval_reason(state, replace(state, **change), {str(state.trigger_message_id)})
        == "recipient_context_changed"
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"pending": True}, "recipient_context_pending"),
        ({"latest_message_id": None}, "recipient_context_pending"),
        ({"latest_message_id": uuid4()}, "recipient_context_changed"),
        ({"health": "blocked"}, "sequence_guard_unavailable"),
    ],
)
def test_static_unready_context_never_provides_approval(change, reason):
    state = replace(ready_state(), **change)
    assert context_approval_reason(state, state, {str(state.trigger_message_id)}) == reason


def test_old_history_page_cannot_supply_approval_revision():
    state = ready_state()
    assert context_approval_reason(state, state, {str(uuid4())}) == "recipient_context_not_visible"


@pytest.mark.parametrize(
    "before,after", [(None, None), (None, ready_state()), (ready_state(), None)]
)
def test_missing_binding_never_authorizes(before, after):
    assert context_approval_reason(before, after, set()) == "recipient_context_changed"


def test_display_removes_control_characters_and_bounds_length():
    account = uuid4()
    assert (
        safe_recipient_display("  Анна\n\u202e\x00  Тест  ", account_id=account, chat_id="1")
        == "Анна Тест"
    )
    assert len(safe_recipient_display("Я" * 500, account_id=account, chat_id="1")) == 120


def test_fallback_is_stable_scoped_and_distinguishes_dialogs():
    account = uuid4()
    a = safe_recipient_display(None, account_id=account, chat_id="12345")
    assert a == safe_recipient_display("\n\u202e", account_id=account, chat_id="12345")
    assert a != safe_recipient_display(None, account_id=account, chat_id="54321")
    assert a != safe_recipient_display(None, account_id=uuid4(), chat_id="12345")
    assert "12345" not in a
