"""Dormant worker boundary for owner-reviewed Alpha installation discovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.application.alpha_access_policy import (
    AlphaDiscoveryCandidate,
    AlphaInstallationState,
    evaluate_discovery,
)


class AlphaDiscoveryWorkerError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AlphaDiscoveryWorkerOutcome(StrEnum):
    DISABLED = "disabled"
    REJECTED = "rejected"
    PENDING_APPROVAL = "pending_approval"


@dataclass(frozen=True, slots=True)
class RunAlphaDiscoveryCommand:
    candidate: AlphaDiscoveryCandidate
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class AlphaDiscoveryWorkerResult:
    outcome: AlphaDiscoveryWorkerOutcome
    reason_code: str
    persistence_attempted: bool
    created: bool
    activation_performed: bool = False
    telegram_delivery_allowed: bool = False

    def __post_init__(self) -> None:
        if self.activation_performed:
            raise AlphaDiscoveryWorkerError("alpha_discovery_cannot_activate")
        if self.telegram_delivery_allowed:
            raise AlphaDiscoveryWorkerError("alpha_discovery_cannot_grant_delivery")
        pending = self.outcome is AlphaDiscoveryWorkerOutcome.PENDING_APPROVAL
        if pending != self.persistence_attempted:
            raise AlphaDiscoveryWorkerError("alpha_discovery_persistence_inconsistent")
        if self.created and not pending:
            raise AlphaDiscoveryWorkerError("alpha_discovery_created_without_persistence")


class AlphaPendingInstallationStore(Protocol):
    async def register_pending_once(
        self,
        candidate: AlphaDiscoveryCandidate,
    ) -> tuple[AlphaInstallationState, bool]:
        """Idempotently persist a pending installation; never activate it."""


class DormantAlphaDiscoveryWorker:
    """Evaluates one candidate only when explicitly enabled by a future runtime."""

    def __init__(self, store: AlphaPendingInstallationStore) -> None:
        self._store = store

    async def run(
        self,
        command: RunAlphaDiscoveryCommand,
    ) -> AlphaDiscoveryWorkerResult:
        if command.enabled is not True:
            return AlphaDiscoveryWorkerResult(
                outcome=AlphaDiscoveryWorkerOutcome.DISABLED,
                reason_code="alpha_discovery_worker_disabled",
                persistence_attempted=False,
                created=False,
            )

        decision = evaluate_discovery(command.candidate)
        if not decision.accepted:
            return AlphaDiscoveryWorkerResult(
                outcome=AlphaDiscoveryWorkerOutcome.REJECTED,
                reason_code=decision.reason_code,
                persistence_attempted=False,
                created=False,
            )
        if decision.initial_state is not AlphaInstallationState.PENDING_APPROVAL:
            raise AlphaDiscoveryWorkerError("alpha_discovery_policy_state_invalid")

        state, created = await self._store.register_pending_once(command.candidate)
        if state is not AlphaInstallationState.PENDING_APPROVAL:
            raise AlphaDiscoveryWorkerError("alpha_discovery_store_activated_installation")
        return AlphaDiscoveryWorkerResult(
            outcome=AlphaDiscoveryWorkerOutcome.PENDING_APPROVAL,
            reason_code=decision.reason_code,
            persistence_attempted=True,
            created=created,
        )
