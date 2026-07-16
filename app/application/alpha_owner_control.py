"""Owner-facing Alpha control contracts with no Telegram provider identifiers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import (
    AlphaInstallationActionCommand,
    AlphaInstallationView,
    AlphaLifecycleService,
    ApproveAlphaInstallationCommand,
)


class AlphaOwnerAction(StrEnum):
    APPROVE = "approve"
    PAUSE = "pause"
    RESUME = "resume"


_ALPHA_FIXTURE_DEPLOYMENT_KEY = "alpha-canary-v1-fixture"
_ALPHA_FIXTURE_REVISION_KEY = "alpha-canary-v1"


class AlphaOwnerControlError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ControlAlphaInstallationCommand:
    workspace_key: str
    installation_ref: str
    action: AlphaOwnerAction
    expected_state: AlphaInstallationState
    confirmed: bool


def alpha_installations_payload(
    installations: tuple[AlphaInstallationView, ...],
) -> dict[str, object]:
    """Build a privacy-bounded owner list identified by label and opaque reference."""

    items = [_installation_payload(item) for item in installations]
    return {
        "count": len(items),
        "items": items,
        "provider_dialog_ids_exposed": False,
    }


async def control_alpha_installation(
    service: AlphaLifecycleService,
    command: ControlAlphaInstallationCommand,
) -> AlphaInstallationView:
    """Apply one confirmed, stale-state-protected reversible owner action."""

    if not command.confirmed:
        raise AlphaOwnerControlError("alpha_owner_confirmation_required")
    expected_by_action = {
        AlphaOwnerAction.APPROVE: AlphaInstallationState.PENDING_APPROVAL,
        AlphaOwnerAction.PAUSE: AlphaInstallationState.ACTIVE,
        AlphaOwnerAction.RESUME: AlphaInstallationState.PAUSED,
    }
    if expected_by_action[command.action] is not command.expected_state:
        raise AlphaOwnerControlError("alpha_owner_expected_state_invalid")
    if command.action is AlphaOwnerAction.APPROVE:
        return await service.approve(
            ApproveAlphaInstallationCommand(
                workspace_key=command.workspace_key,
                installation_key=command.installation_ref,
                deployment_key=_ALPHA_FIXTURE_DEPLOYMENT_KEY,
                revision_key=_ALPHA_FIXTURE_REVISION_KEY,
                expected_state=command.expected_state,
            )
        )
    lifecycle_command = AlphaInstallationActionCommand(
        workspace_key=command.workspace_key,
        installation_key=command.installation_ref,
        expected_state=command.expected_state,
    )
    if command.action is AlphaOwnerAction.PAUSE:
        return await service.pause(lifecycle_command)
    return await service.resume(lifecycle_command)


def alpha_installation_payload(
    installation: AlphaInstallationView,
) -> dict[str, object]:
    return _installation_payload(installation)


def _installation_payload(
    installation: AlphaInstallationView,
) -> dict[str, object]:
    actions: list[str] = []
    if installation.state is AlphaInstallationState.PENDING_APPROVAL:
        actions.append(AlphaOwnerAction.APPROVE.value)
    elif installation.state is AlphaInstallationState.ACTIVE:
        actions.append(AlphaOwnerAction.PAUSE.value)
    elif installation.state is AlphaInstallationState.PAUSED:
        actions.append(AlphaOwnerAction.RESUME.value)
    return {
        "installation_ref": installation.installation_key,
        "group_label": installation.dialog_label,
        "dialog_kind": installation.dialog_kind.value,
        "state": installation.state.value,
        "connector_ready": installation.connector_enabled,
        "deployment_key": installation.deployment_key,
        "revision_key": installation.revision_key,
        "available_actions": actions,
        "limits": {
            "max_turns_per_window": installation.max_turns_per_window,
            "rate_window_seconds": installation.rate_window_seconds,
            "max_tokens_per_turn": installation.max_tokens_per_turn,
        },
        "usage": {
            "reserved_turns": installation.reserved_turns,
            "recorded_tokens": installation.recorded_tokens,
        },
    }
