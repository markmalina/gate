"""Product contracts for the ID-free Alpha owner control plane."""

import asyncio

import pytest
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import AlphaInstallationView, AlphaLifecycleService
from app.application.alpha_owner_control import (
    AlphaOwnerAction,
    AlphaOwnerControlError,
    ControlAlphaInstallationCommand,
    alpha_installations_payload,
    control_alpha_installation,
)
from app.application.canonical_messaging import DialogKind


def _view(state: AlphaInstallationState) -> AlphaInstallationView:
    return AlphaInstallationView(
        installation_key="installation-opaque-ref",
        workspace_key="mark-personal",
        connector_key="telegram_alpha_group",
        connector_enabled=True,
        dialog_kind=DialogKind.SUPERGROUP,
        dialog_label="Alpha Group",
        state=state,
        deployment_key="alpha-canary-v1-fixture",
        revision_key="alpha-canary-v1",
        max_turns_per_window=5,
        rate_window_seconds=60,
        max_tokens_per_turn=2048,
        reserved_turns=2,
        recorded_tokens=120,
    )


class MemoryLifecyclePort:
    def __init__(self, view: AlphaInstallationView) -> None:
        self.view = view
        self.approve_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0

    async def approve(self, command):
        self.approve_calls += 1
        assert command.deployment_key == "alpha-canary-v1-fixture"
        assert command.revision_key == "alpha-canary-v1"
        assert command.expected_state is AlphaInstallationState.PENDING_APPROVAL
        return _view(AlphaInstallationState.ACTIVE)

    async def pause(self, command):
        self.pause_calls += 1
        assert command.expected_state is AlphaInstallationState.ACTIVE
        return _view(AlphaInstallationState.PAUSED)

    async def resume(self, command):
        self.resume_calls += 1
        assert command.expected_state is AlphaInstallationState.PAUSED
        return _view(AlphaInstallationState.ACTIVE)


def test_owner_list_uses_group_label_and_never_exposes_provider_dialog_id() -> None:
    payload = alpha_installations_payload((_view(AlphaInstallationState.ACTIVE),))

    assert payload["count"] == 1
    assert payload["provider_dialog_ids_exposed"] is False
    item = payload["items"][0]
    assert item["group_label"] == "Alpha Group"
    assert item["available_actions"] == ["pause"]
    pending = alpha_installations_payload(
        (_view(AlphaInstallationState.PENDING_APPROVAL),)
    )["items"][0]
    assert pending["available_actions"] == ["approve"]
    assert "provider_dialog_id" not in str(payload["items"])
    assert "workspace_key" not in str(payload["items"])
    assert "connector_key" not in str(payload["items"])


def test_confirmed_pause_and_resume_use_stale_state_fence() -> None:
    port = MemoryLifecyclePort(_view(AlphaInstallationState.ACTIVE))
    service = AlphaLifecycleService(port)  # type: ignore[arg-type]

    approved = asyncio.run(
        control_alpha_installation(
            service,
            ControlAlphaInstallationCommand(
                workspace_key="mark-personal",
                installation_ref="installation-opaque-ref",
                action=AlphaOwnerAction.APPROVE,
                expected_state=AlphaInstallationState.PENDING_APPROVAL,
                confirmed=True,
            ),
        )
    )
    paused = asyncio.run(
        control_alpha_installation(
            service,
            ControlAlphaInstallationCommand(
                workspace_key="mark-personal",
                installation_ref="installation-opaque-ref",
                action=AlphaOwnerAction.PAUSE,
                expected_state=AlphaInstallationState.ACTIVE,
                confirmed=True,
            ),
        )
    )
    resumed = asyncio.run(
        control_alpha_installation(
            service,
            ControlAlphaInstallationCommand(
                workspace_key="mark-personal",
                installation_ref="installation-opaque-ref",
                action=AlphaOwnerAction.RESUME,
                expected_state=AlphaInstallationState.PAUSED,
                confirmed=True,
            ),
        )
    )

    assert approved.state is AlphaInstallationState.ACTIVE
    assert paused.state is AlphaInstallationState.PAUSED
    assert resumed.state is AlphaInstallationState.ACTIVE
    assert port.approve_calls == 1
    assert port.pause_calls == 1
    assert port.resume_calls == 1


def test_unconfirmed_or_mismatched_action_fails_before_lifecycle_write() -> None:
    port = MemoryLifecyclePort(_view(AlphaInstallationState.ACTIVE))
    service = AlphaLifecycleService(port)  # type: ignore[arg-type]

    with pytest.raises(AlphaOwnerControlError, match="confirmation_required"):
        asyncio.run(
            control_alpha_installation(
                service,
                ControlAlphaInstallationCommand(
                    workspace_key="mark-personal",
                    installation_ref="installation-opaque-ref",
                    action=AlphaOwnerAction.PAUSE,
                    expected_state=AlphaInstallationState.ACTIVE,
                    confirmed=False,
                ),
            )
        )
    with pytest.raises(AlphaOwnerControlError, match="expected_state_invalid"):
        asyncio.run(
            control_alpha_installation(
                service,
                ControlAlphaInstallationCommand(
                    workspace_key="mark-personal",
                    installation_ref="installation-opaque-ref",
                    action=AlphaOwnerAction.PAUSE,
                    expected_state=AlphaInstallationState.PAUSED,
                    confirmed=True,
                ),
            )
        )

    assert port.approve_calls == 0
    assert port.pause_calls == 0
    assert port.resume_calls == 0
