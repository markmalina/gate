"""Dormant Gate transport contract for TAKE.

Gate relays creator intent and opaque asset references. It never analyzes media,
transcribes, creates an EditPlan, renders or approves without the creator's
exact confirmation.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol


_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_FORBIDDEN = {"edit_plan", "transcript", "subtitles", "ffmpeg", "shell", "model_prompt"}


class TakeCommandKind(StrEnum):
    CREATE_PROJECT = "CREATE_PROJECT"
    ADD_ASSET = "ADD_ASSET"
    REQUEST_CORRECTION = "REQUEST_CORRECTION"
    SELECT_DIRECTION = "SELECT_DIRECTION"
    APPROVE_VERSION = "APPROVE_VERSION"
    REQUEST_STATUS = "REQUEST_STATUS"


class TakeDecision(StrEnum):
    ASSEMBLE_NOW = "assemble_now"
    WAIT_RESHOOT = "wait_reshoot"
    ALTERNATE_SCENARIO = "alternate_scenario"


@dataclass(frozen=True, slots=True)
class TakeAssetReference:
    asset_id: str
    storage_key: str = field(repr=False)
    sha256: str
    size_bytes: int
    mime_type: str

    def __post_init__(self) -> None:
        _token(self.asset_id, "asset_id")
        _token(self.storage_key, "storage_key", maximum=1024)
        _token(self.mime_type, "mime_type")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("sha256 must be a lowercase SHA-256")
        if self.size_bytes < 1:
            raise ValueError("size_bytes must be positive")

    def validate_workspace(self, take_workspace_id: str) -> None:
        path = PurePosixPath(self.storage_key)
        if (
            path.is_absolute()
            or "\\" in self.storage_key
            or not path.parts
            or path.parts[0] != take_workspace_id
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise ValueError("storage_key must be confined to the TAKE workspace")

    def to_wire(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "storage_key": self.storage_key,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True, slots=True)
class TakeTransportCommand:
    command_id: str
    take_workspace_id: str
    actor_ref: str = field(repr=False)
    source_event_ref: str = field(repr=False)
    kind: TakeCommandKind
    idempotency_key: str
    payload: dict[str, Any] = field(repr=False)
    occurred_at: datetime
    project_id: str | None = None

    def __post_init__(self) -> None:
        uuid.UUID(self.command_id)
        _token(self.take_workspace_id, "take_workspace_id")
        _token(self.actor_ref, "actor_ref")
        _token(self.source_event_ref, "source_event_ref")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.kind is TakeCommandKind.CREATE_PROJECT:
            if self.project_id is not None:
                raise ValueError("CREATE_PROJECT cannot specify project_id")
        elif not self.project_id:
            raise ValueError(f"{self.kind.value} requires project_id")
        _no_take_internals(self.payload)
        expected = take_idempotency_key(
            source_event_ref=self.source_event_ref,
            actor_ref=self.actor_ref,
            kind=self.kind,
        )
        if self.idempotency_key != expected:
            raise ValueError("idempotency key does not match command identity")

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "command_id": self.command_id,
            "workspace_id": self.take_workspace_id,
            "actor_ref": self.actor_ref,
            "source_event_ref": self.source_event_ref,
            "project_id": self.project_id,
            "kind": self.kind.value,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "occurred_at": self.occurred_at.isoformat(),
        }


def take_idempotency_key(
    *,
    source_event_ref: str,
    actor_ref: str,
    kind: TakeCommandKind,
) -> str:
    material = (
        f"take-gate-command-v1\n{source_event_ref}\n{actor_ref}\n{kind.value}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_create_project_command(
    *,
    take_workspace_id: str,
    actor_ref: str,
    source_event_ref: str,
    title: str,
    asset: TakeAssetReference,
    occurred_at: datetime,
) -> TakeTransportCommand:
    _token(title, "title", maximum=200)
    asset.validate_workspace(take_workspace_id)
    kind = TakeCommandKind.CREATE_PROJECT
    return TakeTransportCommand(
        command_id=str(uuid.uuid4()),
        take_workspace_id=take_workspace_id,
        actor_ref=actor_ref,
        source_event_ref=source_event_ref,
        kind=kind,
        idempotency_key=take_idempotency_key(
            source_event_ref=source_event_ref,
            actor_ref=actor_ref,
            kind=kind,
        ),
        payload={"title": title, "asset": asset.to_wire()},
        occurred_at=occurred_at,
    )


def build_approval_command(
    *,
    take_workspace_id: str,
    actor_ref: str,
    source_event_ref: str,
    project_id: str,
    version_id: str,
    video_sha256: str,
    confirmation: bool,
    occurred_at: datetime,
) -> TakeTransportCommand:
    if confirmation is not True:
        raise ValueError("TAKE approval requires explicit confirmation")
    _token(version_id, "version_id")
    if not _SHA256.fullmatch(video_sha256):
        raise ValueError("video_sha256 must be a lowercase SHA-256")
    kind = TakeCommandKind.APPROVE_VERSION
    return TakeTransportCommand(
        command_id=str(uuid.uuid4()),
        take_workspace_id=take_workspace_id,
        actor_ref=actor_ref,
        source_event_ref=source_event_ref,
        project_id=project_id,
        kind=kind,
        idempotency_key=take_idempotency_key(
            source_event_ref=source_event_ref,
            actor_ref=actor_ref,
            kind=kind,
        ),
        payload={
            "version_id": version_id,
            "video_sha256": video_sha256,
            "confirmation": True,
        },
        occurred_at=occurred_at,
    )


class TakeTransportPort(Protocol):
    async def submit(self, command: TakeTransportCommand) -> dict[str, Any]:
        """Submit one idempotent command; implementation must not edit media."""
        ...


def validate_take_event(raw: dict[str, Any]) -> None:
    required = {
        "schema_version", "event_id", "command_id", "workspace_id",
        "project_id", "take_version_id", "kind", "payload", "created_at",
    }
    if set(raw) != required or raw.get("schema_version") != "1":
        raise ValueError("invalid TAKE event envelope")
    _no_take_internals(raw["payload"])


def _no_take_internals(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN & set(value)
        if forbidden:
            raise ValueError("Gate cannot carry TAKE internals")
        for nested in value.values():
            _no_take_internals(nested)
    elif isinstance(value, list):
        for nested in value:
            _no_take_internals(nested)


def _token(value: str, field_name: str, maximum: int = 255) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded non-empty token")
