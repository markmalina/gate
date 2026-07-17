"""Tests for the dormant, owner-reviewed Alpha discovery worker."""

import pytest
from app.application.alpha_access_policy import (
    AlphaDialogKind,
    AlphaDiscoveryCandidate,
    AlphaInstallationState,
)
from app.application.alpha_private_discovery_worker import (
    AlphaDiscoveryWorkerError,
    AlphaDiscoveryWorkerOutcome,
    DormantAlphaDiscoveryWorker,
    RunAlphaDiscoveryCommand,
)


class FakePendingInstallationStore:
    def __init__(
        self,
        state: AlphaInstallationState = AlphaInstallationState.PENDING_APPROVAL,
    ) -> None:
        self.state = state
        self.calls: list[AlphaDiscoveryCandidate] = []

    async def register_pending_once(
        self,
        candidate: AlphaDiscoveryCandidate,
    ) -> tuple[AlphaInstallationState, bool]:
        self.calls.append(candidate)
        return self.state, len(self.calls) == 1


def candidate(kind: AlphaDialogKind = AlphaDialogKind.PRIVATE) -> AlphaDiscoveryCandidate:
    return AlphaDiscoveryCandidate(
        workspace_key="owner-workspace",
        connector_key="alpha-telegram",
        dialog_ref="private-provider-ref-sensitive",
        dialog_kind=kind,
        dialog_label="Private Alpha",
    )


@pytest.mark.anyio
async def test_worker_is_disabled_by_default_and_does_not_persist() -> None:
    store = FakePendingInstallationStore()
    worker = DormantAlphaDiscoveryWorker(store)

    result = await worker.run(RunAlphaDiscoveryCommand(candidate()))

    assert result.outcome is AlphaDiscoveryWorkerOutcome.DISABLED
    assert result.persistence_attempted is False
    assert result.activation_performed is False
    assert result.telegram_delivery_allowed is False
    assert store.calls == []


@pytest.mark.anyio
async def test_enabled_private_discovery_only_persists_pending_approval() -> None:
    store = FakePendingInstallationStore()
    worker = DormantAlphaDiscoveryWorker(store)

    result = await worker.run(
        RunAlphaDiscoveryCommand(candidate(), enabled=True),
    )

    assert result.outcome is AlphaDiscoveryWorkerOutcome.PENDING_APPROVAL
    assert result.reason_code == "alpha_owner_approval_required"
    assert result.created is True
    assert result.activation_performed is False
    assert result.telegram_delivery_allowed is False
    assert len(store.calls) == 1


@pytest.mark.anyio
async def test_channel_candidate_is_rejected_without_persistence() -> None:
    store = FakePendingInstallationStore()
    worker = DormantAlphaDiscoveryWorker(store)

    result = await worker.run(
        RunAlphaDiscoveryCommand(
            candidate(AlphaDialogKind.CHANNEL),
            enabled=True,
        ),
    )

    assert result.outcome is AlphaDiscoveryWorkerOutcome.REJECTED
    assert result.reason_code == "alpha_channel_not_supported"
    assert store.calls == []


@pytest.mark.anyio
async def test_store_cannot_silently_activate_discovered_installation() -> None:
    store = FakePendingInstallationStore(AlphaInstallationState.ACTIVE)
    worker = DormantAlphaDiscoveryWorker(store)

    with pytest.raises(
        AlphaDiscoveryWorkerError,
        match="alpha_discovery_store_activated_installation",
    ):
        await worker.run(
            RunAlphaDiscoveryCommand(candidate(), enabled=True),
        )


def test_sensitive_provider_reference_is_hidden_from_repr() -> None:
    command = RunAlphaDiscoveryCommand(candidate())

    assert "private-provider-ref-sensitive" not in repr(command)
