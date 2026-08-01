"""Safety tests for the isolated BECOME transport connector bootstrap."""

import pytest
from app.infrastructure.become_connector_binding import (
    BecomeConnectorBindingError,
    become_connector_transition,
)


@pytest.mark.parametrize(
    ("connector_kind", "credential_ref", "enabled", "expected"),
    [
        (None, None, False, "created"),
        ("telegram_bot", None, False, "repaired"),
        ("telegram_bot", "TELEGRAM_BECOME_BOT_TOKEN", False, "repaired"),
        ("telegram_bot", "TELEGRAM_BECOME_BOT_TOKEN", True, "already_current"),
    ],
)
def test_become_connector_accepts_only_absent_exact_or_repairable_state(
    connector_kind: str | None,
    credential_ref: str | None,
    enabled: bool,
    expected: str,
) -> None:
    assert (
        become_connector_transition(
            connector_kind=connector_kind,
            credential_secret_ref=credential_ref,
            is_enabled=enabled,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("connector_kind", "credential_ref", "enabled", "error_code"),
    [
        (None, "TELEGRAM_BECOME_BOT_TOKEN", False, "become_connector_partial_identity"),
        (None, None, True, "become_connector_partial_identity"),
        ("telegram_business", None, False, "become_connector_kind_conflict"),
        (
            "telegram_bot",
            "TELEGRAM_ALPHA_BOT_TOKEN",
            True,
            "become_connector_credential_conflict",
        ),
        (
            "telegram_bot",
            "TELEGRAM_BETA_BOT_TOKEN",
            True,
            "become_connector_credential_conflict",
        ),
    ],
)
def test_become_connector_rejects_partial_shared_or_conflicting_identity(
    connector_kind: str | None,
    credential_ref: str | None,
    enabled: bool,
    error_code: str,
) -> None:
    with pytest.raises(BecomeConnectorBindingError, match=error_code):
        become_connector_transition(
            connector_kind=connector_kind,
            credential_secret_ref=credential_ref,
            is_enabled=enabled,
        )
