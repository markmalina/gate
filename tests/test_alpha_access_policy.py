"""Alpha group/private discovery and addressing policy tests."""

import pytest

from app.application.alpha_access_policy import (
    AlphaAccessPolicyError,
    AlphaAddressingSignal,
    AlphaDialogKind,
    AlphaDiscoveryCandidate,
    AlphaInstallationState,
    AlphaInboundTurn,
    AlphaTurnDecision,
    evaluate_discovery,
    evaluate_inbound_turn,
)


def _candidate(kind: AlphaDialogKind) -> AlphaDiscoveryCandidate:
    return AlphaDiscoveryCandidate(
        workspace_key="owner-workspace",
        connector_key="alpha-telegram",
        dialog_ref="-opaque-provider-ref",
        dialog_kind=kind,
        dialog_label="Alpha test",
    )


@pytest.mark.parametrize(
    "kind",
    [
        AlphaDialogKind.PRIVATE,
        AlphaDialogKind.GROUP,
        AlphaDialogKind.SUPERGROUP,
    ],
)
def test_supported_dialogs_are_discovered_but_never_auto_activated(
    kind: AlphaDialogKind,
) -> None:
    decision = evaluate_discovery(_candidate(kind))

    assert decision.accepted is True
    assert decision.initial_state is AlphaInstallationState.PENDING_APPROVAL
    assert decision.reason_code == "alpha_owner_approval_required"


def test_channels_are_default_denied() -> None:
    decision = evaluate_discovery(_candidate(AlphaDialogKind.CHANNEL))

    assert decision.accepted is False
    assert decision.initial_state is None


@pytest.mark.parametrize(
    ("kind", "signal"),
    [
        (AlphaDialogKind.GROUP, AlphaAddressingSignal.COMMAND),
        (AlphaDialogKind.GROUP, AlphaAddressingSignal.MENTION),
        (AlphaDialogKind.SUPERGROUP, AlphaAddressingSignal.REPLY_TO_ALPHA),
        (AlphaDialogKind.PRIVATE, AlphaAddressingSignal.DIRECT_MESSAGE),
        (AlphaDialogKind.PRIVATE, AlphaAddressingSignal.COMMAND),
    ],
)
def test_active_installations_accept_only_addressed_human_turns(
    kind: AlphaDialogKind,
    signal: AlphaAddressingSignal,
) -> None:
    decision = evaluate_inbound_turn(
        AlphaInboundTurn(
            dialog_kind=kind,
            installation_state=AlphaInstallationState.ACTIVE,
            signal=signal,
            sender_is_bot_or_service=False,
            connector_ready=True,
            provider_ready=True,
            billing_admitted=True,
        )
    )

    assert decision.admitted is True
    assert decision.telegram_delivery_allowed is False


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"installation_state": AlphaInstallationState.PENDING_APPROVAL},
            "alpha_installation_not_active",
        ),
        ({"sender_is_bot_or_service": True}, "alpha_sender_not_human"),
        ({"connector_ready": False}, "alpha_connector_not_ready"),
        ({"provider_ready": False}, "alpha_provider_not_ready"),
        ({"billing_admitted": False}, "alpha_billing_not_admitted"),
    ],
)
def test_every_runtime_gate_fails_closed(
    change: dict[str, object],
    reason: str,
) -> None:
    values: dict[str, object] = {
        "dialog_kind": AlphaDialogKind.PRIVATE,
        "installation_state": AlphaInstallationState.ACTIVE,
        "signal": AlphaAddressingSignal.DIRECT_MESSAGE,
        "sender_is_bot_or_service": False,
        "connector_ready": True,
        "provider_ready": True,
        "billing_admitted": True,
    }
    values.update(change)
    turn = AlphaInboundTurn(**values)  # type: ignore[arg-type]

    decision = evaluate_inbound_turn(turn)
    assert decision == AlphaTurnDecision(False, reason)


def test_group_direct_message_signal_is_not_admitted() -> None:
    decision = evaluate_inbound_turn(
        AlphaInboundTurn(
            dialog_kind=AlphaDialogKind.GROUP,
            installation_state=AlphaInstallationState.ACTIVE,
            signal=AlphaAddressingSignal.DIRECT_MESSAGE,
            sender_is_bot_or_service=False,
            connector_ready=True,
            provider_ready=True,
            billing_admitted=True,
        )
    )

    assert decision.admitted is False
    assert decision.reason_code == "alpha_not_addressed"


def test_policy_can_never_grant_telegram_delivery() -> None:
    with pytest.raises(
        AlphaAccessPolicyError,
        match="alpha_policy_cannot_grant_delivery",
    ):
        AlphaTurnDecision(
            admitted=True,
            reason_code="invalid",
            telegram_delivery_allowed=True,
        )
