"""Provider-disabled BECOME voice correlation and chain contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)

BECOME_VOICE_CORRELATION_SCHEMA = "gate.become-voice-correlation@1"
BECOME_CHAIN_SLOT_SCHEMA = "gate.become-chain-slot@1"
BECOME_CHAIN_STAGES = ("relations", "mayaki", "oko", "take")


class BecomeChainStage(StrEnum):
    RELATIONS = "relations"
    MAYAKI = "mayaki"
    OKO = "oko"
    TAKE = "take"


class BecomeChainSlotState(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    REQUESTED = "requested"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PreparedBecomeVoiceCorrelation:
    correlation_ref: str
    slot_count: int
    duplicate: bool


@dataclass(frozen=True, slots=True)
class ConfirmedBecomeTranscriptReceipt:
    correlation_ref: str
    transcript_digest: str
    transcript_expires_at: datetime
    relations_slot_state: BecomeChainSlotState
    duplicate: bool
    transcript: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.transcript is not None:
            raise BecomeControlledIntakeError(
                "become_transcript_receipt_must_be_content_free"
            )


class BecomeVoiceChainStore(Protocol):
    async def prepare(
        self,
        *,
        workspace_key: str,
        intake_key: str,
        prepared_at: datetime,
    ) -> PreparedBecomeVoiceCorrelation: ...

    async def confirm_transcript(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        transcript: str,
        confirmed_at: datetime,
    ) -> ConfirmedBecomeTranscriptReceipt: ...


class PrepareBecomeVoiceCorrelationService:
    """Create one correlation root and four blocked chain slots."""

    def __init__(self, store: BecomeVoiceChainStore) -> None:
        self._store = store

    async def execute(
        self,
        *,
        workspace_key: str,
        intake_key: str,
        prepared_at: datetime,
    ) -> PreparedBecomeVoiceCorrelation:
        if prepared_at.tzinfo is None:
            raise BecomeControlledIntakeError(
                "become_voice_correlation_timestamp_invalid"
            )
        return await self._store.prepare(
            workspace_key=workspace_key,
            intake_key=intake_key,
            prepared_at=prepared_at,
        )


class ConfirmBecomeTranscriptService:
    """Confirm text internally and return only a content-free receipt."""

    def __init__(self, store: BecomeVoiceChainStore) -> None:
        self._store = store

    async def execute(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        transcript: str,
        confirmed_at: datetime,
    ) -> ConfirmedBecomeTranscriptReceipt:
        if confirmed_at.tzinfo is None:
            raise BecomeControlledIntakeError(
                "become_transcript_confirmation_timestamp_invalid"
            )
        if not transcript or transcript != transcript.strip():
            raise BecomeControlledIntakeError(
                "become_transcript_confirmation_invalid"
            )
        receipt = await self._store.confirm_transcript(
            workspace_key=workspace_key,
            correlation_ref=correlation_ref,
            transcript=transcript,
            confirmed_at=confirmed_at,
        )
        if receipt.relations_slot_state is not BecomeChainSlotState.READY:
            raise BecomeControlledIntakeError(
                "become_transcript_relations_slot_not_ready"
            )
        return receipt
