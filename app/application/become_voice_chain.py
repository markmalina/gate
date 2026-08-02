"""Provider-disabled BECOME voice correlation and chain contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)

BECOME_VOICE_CORRELATION_SCHEMA = "gate.become-voice-correlation@1"
BECOME_CHAIN_SLOT_SCHEMA = "gate.become-chain-slot@1"
BECOME_CHAIN_STAGES = ("relations", "mayaki", "oko", "take")
_DIRECT_PRIVATE_KEYS = {
    "audio",
    "content",
    "media",
    "raw",
    "text",
    "transcript",
    "video",
}
_SENSITIVE_KEY = re.compile(
    r"(^|_)(email|phone|username|token|secret|credential|"
    r"provider_payload|raw_text|transcript_text|media_bytes|pii|"
    r"personal_data)($|_)",
    re.IGNORECASE,
)


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


JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReservedBecomeChainStage:
    correlation_ref: str
    stage: BecomeChainStage
    state: BecomeChainSlotState
    request_digest: str
    source_sha: str
    duplicate: bool
    reconciliation_required: bool
    request_payload: JsonObject = field(repr=False, compare=False)
    receipt_digest: str | None = None
    receipt_payload: JsonObject | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class FinalizedBecomeChainStage:
    correlation_ref: str
    stage: BecomeChainStage
    state: BecomeChainSlotState
    request_digest: str
    receipt_digest: str
    duplicate: bool


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

    async def reserve_stage(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        stage: BecomeChainStage,
        request_payload: JsonObject,
        source_sha: str,
        mapping_digest: str | None,
        requested_at: datetime,
    ) -> ReservedBecomeChainStage: ...

    async def finalize_stage(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        stage: BecomeChainStage,
        request_digest: str,
        receipt_payload: JsonObject,
        state: BecomeChainSlotState,
        finished_at: datetime,
    ) -> FinalizedBecomeChainStage: ...


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



class ReserveBecomeChainStageService:
    """Reserve one exact request; never retry an ambiguous prior outcome."""

    def __init__(self, store: BecomeVoiceChainStore) -> None:
        self._store = store

    async def execute(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        stage: BecomeChainStage,
        request_payload: JsonObject,
        source_sha: str,
        requested_at: datetime,
        mapping_digest: str | None = None,
    ) -> ReservedBecomeChainStage:
        if requested_at.tzinfo is None:
            raise BecomeControlledIntakeError(
                "become_chain_request_timestamp_invalid"
            )
        _validate_source_sha(source_sha)
        _assert_content_free_payload(request_payload)
        if stage is BecomeChainStage.TAKE:
            if mapping_digest is None or not _is_digest(mapping_digest):
                raise BecomeControlledIntakeError(
                    "become_chain_take_mapping_required"
                )
        elif mapping_digest is not None:
            raise BecomeControlledIntakeError(
                "become_chain_mapping_stage_invalid"
            )
        return await self._store.reserve_stage(
            workspace_key=workspace_key,
            correlation_ref=correlation_ref,
            stage=stage,
            request_payload=request_payload,
            source_sha=source_sha,
            mapping_digest=mapping_digest,
            requested_at=requested_at,
        )


class FinalizeBecomeChainStageService:
    """Persist one exact content-free receipt and advance no provider itself."""

    _TERMINAL_STATES = {
        BecomeChainSlotState.COMPLETED,
        BecomeChainSlotState.FAILED,
        BecomeChainSlotState.UNKNOWN,
    }

    def __init__(self, store: BecomeVoiceChainStore) -> None:
        self._store = store

    async def execute(
        self,
        *,
        workspace_key: str,
        correlation_ref: str,
        stage: BecomeChainStage,
        request_digest: str,
        receipt_payload: JsonObject,
        state: BecomeChainSlotState,
        finished_at: datetime,
    ) -> FinalizedBecomeChainStage:
        if finished_at.tzinfo is None:
            raise BecomeControlledIntakeError(
                "become_chain_receipt_timestamp_invalid"
            )
        if state not in self._TERMINAL_STATES:
            raise BecomeControlledIntakeError(
                "become_chain_receipt_state_invalid"
            )
        if not _is_digest(request_digest):
            raise BecomeControlledIntakeError(
                "become_chain_request_digest_invalid"
            )
        _assert_content_free_payload(receipt_payload)
        return await self._store.finalize_stage(
            workspace_key=workspace_key,
            correlation_ref=correlation_ref,
            stage=stage,
            request_digest=request_digest,
            receipt_payload=receipt_payload,
            state=state,
            finished_at=finished_at,
        )


def canonical_payload_digest(payload: JsonObject) -> str:
    _assert_content_free_payload(payload)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def take_mapping_digest(
    *,
    oko_result_receipt_ref: str,
    take_source_asset_ref: str,
) -> str:
    for value in (oko_result_receipt_ref, take_source_asset_ref):
        if (
            not value
            or value != value.strip()
            or len(value) > 256
        ):
            raise BecomeControlledIntakeError(
                "become_chain_take_mapping_invalid"
            )
    return canonical_payload_digest(
        {
            "oko_result_receipt_ref": oko_result_receipt_ref,
            "take_source_asset_ref": take_source_asset_ref,
        }
    )


def _validate_source_sha(source_sha: str) -> None:
    if (
        len(source_sha) != 40
        or any(character not in "0123456789abcdef" for character in source_sha)
    ):
        raise BecomeControlledIntakeError(
            "become_chain_source_sha_invalid"
        )


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _assert_content_free_payload(payload: JsonObject) -> None:
    pending: list[object] = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
                if (
                    normalized in _DIRECT_PRIVATE_KEYS
                    or _SENSITIVE_KEY.search(normalized)
                ):
                    raise BecomeControlledIntakeError(
                        "become_chain_payload_contains_private_content"
                    )
                pending.append(nested)
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            continue
        else:
            raise BecomeControlledIntakeError(
                "become_chain_payload_type_invalid"
            )
