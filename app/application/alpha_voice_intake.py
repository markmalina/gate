"""Queue-only intake orchestration for future Alpha voice transcription.

The service makes a pure access/media decision first.  Only an eligible voice message
crosses the persistence port; terminal policy outcomes never create provider work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.application.alpha_access import AlphaAccessReason
from app.application.alpha_voice import (
    AlphaVoiceIntakeDisposition,
    EvaluateAlphaVoiceCommand,
    TelegramVoiceMetadata,
    TranscriptionJobKey,
    evaluate_alpha_voice,
)
from app.application.canonical_messaging import DialogKind


class AlphaVoiceQueueDisposition(StrEnum):
    QUEUED = "queued"
    DUPLICATE = "duplicate"
    TERMINAL = "terminal"


class AlphaVoiceQueueCode(StrEnum):
    QUEUED = "alpha_voice_job_queued"
    DUPLICATE = "alpha_voice_job_duplicate"
    WORKSPACE_UNAVAILABLE = "alpha_voice_workspace_unavailable"
    INSTALLATION_UNAVAILABLE = "alpha_voice_installation_unavailable"
    INSTALLATION_INACTIVE = "alpha_voice_installation_inactive"
    CONNECTOR_INACTIVE = "alpha_voice_connector_inactive"
    DIALOG_SCOPE_MISMATCH = "alpha_voice_dialog_scope_mismatch"
    MESSAGE_UNAVAILABLE = "alpha_voice_message_unavailable"
    MESSAGE_NOT_INBOUND = "alpha_voice_message_not_inbound"
    MEDIA_CONFLICT = "alpha_voice_media_conflict"


@dataclass(frozen=True, slots=True)
class QueueAlphaVoiceJobCommand:
    """Validated media plus opaque installation and canonical identities."""

    job_key: TranscriptionJobKey = field(repr=False)
    installation_ref: str = field(repr=False)
    dialog_kind: DialogKind
    metadata: TelegramVoiceMetadata = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.installation_ref, str)
            or not self.installation_ref
            or self.installation_ref != self.installation_ref.strip()
            or len(self.installation_ref) > 128
        ):
            raise ValueError("alpha_voice_installation_ref_invalid")
        if self.dialog_kind not in {
            DialogKind.PRIVATE,
            DialogKind.GROUP,
            DialogKind.SUPERGROUP,
        }:
            raise ValueError("alpha_voice_dialog_kind_invalid")


@dataclass(frozen=True, slots=True)
class AlphaVoiceQueueResult:
    disposition: AlphaVoiceQueueDisposition
    code: AlphaVoiceQueueCode
    job_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        persisted = self.disposition in {
            AlphaVoiceQueueDisposition.QUEUED,
            AlphaVoiceQueueDisposition.DUPLICATE,
        }
        if persisted != (self.job_id is not None):
            raise ValueError("alpha_voice_queue_result_inconsistent")
        if self.job_id is not None and (
            not self.job_id
            or self.job_id != self.job_id.strip()
            or len(self.job_id) > 128
        ):
            raise ValueError("alpha_voice_job_id_invalid")
        if self.disposition is AlphaVoiceQueueDisposition.QUEUED:
            expected = AlphaVoiceQueueCode.QUEUED
        elif self.disposition is AlphaVoiceQueueDisposition.DUPLICATE:
            expected = AlphaVoiceQueueCode.DUPLICATE
        else:
            expected = self.code
            if expected in {AlphaVoiceQueueCode.QUEUED, AlphaVoiceQueueCode.DUPLICATE}:
                raise ValueError("alpha_voice_terminal_code_invalid")
        if self.code is not expected:
            raise ValueError("alpha_voice_queue_code_inconsistent")


class AlphaVoiceQueueStore(Protocol):
    """Atomic PostgreSQL boundary; implementations perform no external calls."""

    async def enqueue_once(
        self, command: QueueAlphaVoiceJobCommand
    ) -> AlphaVoiceQueueResult: ...


@dataclass(frozen=True, slots=True)
class AlphaVoiceIntakeResult:
    disposition: AlphaVoiceQueueDisposition
    code: str
    access_reason: AlphaAccessReason
    terminal: bool
    retryable: bool
    duplicate: bool
    job_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        persisted = self.disposition in {
            AlphaVoiceQueueDisposition.QUEUED,
            AlphaVoiceQueueDisposition.DUPLICATE,
        }
        if persisted != (self.job_id is not None):
            raise ValueError("alpha_voice_intake_result_inconsistent")
        if persisted and (self.terminal or self.retryable):
            raise ValueError("alpha_voice_persisted_result_inconsistent")
        if not persisted and (not self.terminal or self.retryable):
            raise ValueError("alpha_voice_terminal_result_inconsistent")
        if self.duplicate != (
            self.disposition is AlphaVoiceQueueDisposition.DUPLICATE
        ):
            raise ValueError("alpha_voice_duplicate_result_inconsistent")


class AlphaVoiceIntakeService:
    """Evaluate and durably enqueue at most one transcription operation."""

    def __init__(self, store: AlphaVoiceQueueStore) -> None:
        self._store = store

    async def intake(self, command: EvaluateAlphaVoiceCommand) -> AlphaVoiceIntakeResult:
        decision = evaluate_alpha_voice(command)
        if decision.disposition is AlphaVoiceIntakeDisposition.TERMINAL:
            return AlphaVoiceIntakeResult(
                disposition=AlphaVoiceQueueDisposition.TERMINAL,
                code=decision.code.value,
                access_reason=decision.access_reason,
                terminal=True,
                retryable=False,
                duplicate=False,
            )

        if decision.job_key is None or decision.metadata is None:
            raise RuntimeError("eligible Alpha voice decision omitted its queue payload")
        queued = await self._store.enqueue_once(
            QueueAlphaVoiceJobCommand(
                job_key=decision.job_key,
                installation_ref=command.access.installation.installation_ref,
                dialog_kind=command.access.dialog_kind,
                metadata=decision.metadata,
            )
        )
        return AlphaVoiceIntakeResult(
            disposition=queued.disposition,
            code=queued.code.value,
            access_reason=decision.access_reason,
            terminal=queued.disposition is AlphaVoiceQueueDisposition.TERMINAL,
            retryable=False,
            duplicate=queued.disposition is AlphaVoiceQueueDisposition.DUPLICATE,
            job_id=queued.job_id,
        )
