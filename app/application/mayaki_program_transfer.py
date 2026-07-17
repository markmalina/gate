"""Portable Mayaki program-event transfer and write-free replay validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Literal

from app.application.mayaki_program import (
    ApplyProgramEventCommand,
    MayakiProgramEventV1,
    ProgramSession,
    ProgramState,
    apply_program_event,
)
from app.application.relationship_crm import RelationshipCrmDomainError

MAYAKI_PROGRAM_TRANSFER_SCHEMA: Literal["mayaki.program.transfer.v1"] = (
    "mayaki.program.transfer.v1"
)


@dataclass(frozen=True, slots=True)
class MayakiProgramTransferV1:
    workspace_key: str
    space_key: str
    program_key: str
    participant_key: str = field(repr=False)
    events: tuple[MayakiProgramEventV1, ...] = field(repr=False)
    generated_at: datetime
    evidence_sha256: str
    schema: Literal["mayaki.program.transfer.v1"] = MAYAKI_PROGRAM_TRANSFER_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MAYAKI_PROGRAM_TRANSFER_SCHEMA:
            raise RelationshipCrmDomainError("mayaki_transfer_schema_invalid")
        if not self.events or len(self.events) > 10_000:
            raise RelationshipCrmDomainError("mayaki_transfer_event_count_invalid")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise RelationshipCrmDomainError("mayaki_transfer_time_timezone_required")
        if len(self.evidence_sha256) != 64:
            raise RelationshipCrmDomainError("mayaki_transfer_digest_invalid")
        _validate_scope(self)
        if _digest(self.events) != self.evidence_sha256:
            raise RelationshipCrmDomainError("mayaki_transfer_digest_mismatch")


@dataclass(frozen=True, slots=True)
class MayakiProgramImportDryRun:
    event_count: int
    final_state: ProgramState
    final_revision: int
    evidence_sha256: str
    writes_performed: bool = False
    notification_allowed: bool = False
    telegram_delivery_allowed: bool = False

    def __post_init__(self) -> None:
        if any(
            (
                self.writes_performed,
                self.notification_allowed,
                self.telegram_delivery_allowed,
            )
        ):
            raise RelationshipCrmDomainError("mayaki_import_dry_run_side_effect")


def build_mayaki_program_transfer(
    *,
    workspace_key: str,
    space_key: str,
    program_key: str,
    participant_key: str,
    events: tuple[MayakiProgramEventV1, ...],
    generated_at: datetime,
) -> MayakiProgramTransferV1:
    return MayakiProgramTransferV1(
        workspace_key=workspace_key,
        space_key=space_key,
        program_key=program_key,
        participant_key=participant_key,
        events=events,
        generated_at=generated_at,
        evidence_sha256=_digest(events),
    )


def dry_run_mayaki_program_import(
    transfer: MayakiProgramTransferV1,
) -> MayakiProgramImportDryRun:
    """Replay the portable event stream entirely in memory."""

    _validate_scope(transfer)
    event_keys = [event.event_key for event in transfer.events]
    if len(set(event_keys)) != len(event_keys):
        raise RelationshipCrmDomainError("mayaki_transfer_event_key_duplicate")
    expected_revisions = tuple(range(1, len(transfer.events) + 1))
    actual_revisions = tuple(event.revision for event in transfer.events)
    if actual_revisions != expected_revisions:
        raise RelationshipCrmDomainError("mayaki_transfer_revision_sequence_invalid")

    current: ProgramSession | None = None
    for event in transfer.events:
        result = apply_program_event(
            current,
            ApplyProgramEventCommand(
                event_key=event.event_key,
                workspace_key=event.workspace_key,
                space_key=event.space_key,
                program_key=event.program_key,
                participant_key=event.participant_key,
                kind=event.kind,
                expected_revision=event.revision - 1,
                actor_key="mayaki-transfer-dry-run",
                confirmed=True,
                occurred_at=event.occurred_at,
            ),
        )
        current = result.session
    if current is None:
        raise RelationshipCrmDomainError("mayaki_transfer_session_missing")
    return MayakiProgramImportDryRun(
        event_count=len(transfer.events),
        final_state=current.state,
        final_revision=current.revision,
        evidence_sha256=transfer.evidence_sha256,
    )


def _validate_scope(transfer: MayakiProgramTransferV1) -> None:
    if any(
        (
            event.workspace_key,
            event.space_key,
            event.program_key,
            event.participant_key,
        )
        != (
            transfer.workspace_key,
            transfer.space_key,
            transfer.program_key,
            transfer.participant_key,
        )
        for event in transfer.events
    ):
        raise RelationshipCrmDomainError("mayaki_transfer_scope_mismatch")


def _digest(events: tuple[MayakiProgramEventV1, ...]) -> str:
    material = "\n".join(
        "|".join(
            (
                event.event_key,
                event.workspace_key,
                event.space_key,
                event.program_key,
                event.participant_key,
                event.kind.value,
                str(event.revision),
                event.occurred_at.isoformat(),
            )
        )
        for event in events
    )
    return sha256(material.encode()).hexdigest()
