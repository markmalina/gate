"""Tests for the no-duplicate Beta connector binding transition."""

import pytest
from app.application.alpha_canary import AlphaCanaryError, AlphaMembershipAction
from app.application.alpha_group import AlphaInstallationState
from app.infrastructure.alpha_canary import pinned_installation_membership_transition
from app.infrastructure.beta_connector_binding import (
    BetaConnectorBindingError,
    beta_binding_transition,
)


def test_alpha_installation_can_move_to_beta_without_duplicates() -> None:
    assert (
        beta_binding_transition(
            current_connector_key="telegram_alpha_group",
            dialog_connector_key="telegram_alpha_group",
            foreign_beta_installations=0,
            foreign_beta_dialogs=0,
        )
        == "rebound"
    )


def test_exact_beta_binding_is_idempotent() -> None:
    assert (
        beta_binding_transition(
            current_connector_key="telegram_beta_group",
            dialog_connector_key="telegram_beta_group",
            foreign_beta_installations=0,
            foreign_beta_dialogs=0,
        )
        == "already_bound"
    )


@pytest.mark.parametrize(
    ("current", "dialog", "installations", "dialogs"),
    [
        ("telegram_business", "telegram_business", 0, 0),
        ("telegram_alpha_group", "telegram_beta_group", 0, 0),
        ("telegram_beta_group", "telegram_alpha_group", 0, 0),
        ("telegram_alpha_group", "telegram_alpha_group", 1, 0),
        ("telegram_alpha_group", "telegram_alpha_group", 0, 1),
    ],
)
def test_mismatch_or_duplicate_fails_closed(
    current: str,
    dialog: str,
    installations: int,
    dialogs: int,
) -> None:
    with pytest.raises(BetaConnectorBindingError):
        beta_binding_transition(
            current_connector_key=current,
            dialog_connector_key=dialog,
            foreign_beta_installations=installations,
            foreign_beta_dialogs=dialogs,
        )


@pytest.mark.parametrize(
    ("state", "action", "expected"),
    [
        (AlphaInstallationState.ACTIVE, AlphaMembershipAction.PRESENT, "unchanged"),
        (AlphaInstallationState.ACTIVE, AlphaMembershipAction.REMOVED, "pause"),
        (AlphaInstallationState.PAUSED, AlphaMembershipAction.PRESENT, "resume"),
        (AlphaInstallationState.PAUSED, AlphaMembershipAction.REMOVED, "unchanged"),
        (AlphaInstallationState.REVOKED, AlphaMembershipAction.REMOVED, "unchanged"),
    ],
)
def test_pinned_beta_group_membership_is_reversible(
    state: AlphaInstallationState,
    action: AlphaMembershipAction,
    expected: str,
) -> None:
    assert pinned_installation_membership_transition(
        state=state,
        action=action,
    ) == expected


def test_explicitly_revoked_beta_installation_stays_terminal() -> None:
    with pytest.raises(
        AlphaCanaryError,
        match="alpha_canary_pinned_installation_not_resumable",
    ):
        pinned_installation_membership_transition(
            state=AlphaInstallationState.REVOKED,
            action=AlphaMembershipAction.PRESENT,
        )
