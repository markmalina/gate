"""Transport-neutral contracts for versioned, delivery-free agent proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.application.work_ownership import AgentKey

_GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class AgentDeploymentState(StrEnum):
    """Lifecycle of an immutable instruction deployment."""

    STAGED = "staged"
    ACTIVE = "active"
    PAUSED = "paused"


class AgentResultKind(StrEnum):
    """Closed result vocabulary emitted by every agent runtime."""

    REPLY = "reply"
    HANDOFF = "handoff"
    NO_REPLY = "no_reply"


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Stable product agent identity, independent of prompts and models."""

    key: AgentKey
    display_name: str

    def __post_init__(self) -> None:
        _require_text(self.display_name, field_name="display_name", maximum=128)


@dataclass(frozen=True, slots=True)
class InstructionRevision:
    """Immutable reviewed instruction identity from a Git supply chain."""

    revision_key: str
    source_repository: str
    source_path: str
    source_commit_sha: str = field(repr=False)
    content_sha256: str = field(repr=False)
    schema_version: str = "agent-bundle-v1"

    def __post_init__(self) -> None:
        _require_token(self.revision_key, field_name="revision_key", maximum=128)
        _require_token(self.source_repository, field_name="source_repository", maximum=255)
        _require_token(self.source_path, field_name="source_path", maximum=512)
        _require_token(self.schema_version, field_name="schema_version", maximum=64)
        if _GIT_SHA_PATTERN.fullmatch(self.source_commit_sha) is None:
            raise ValueError("source_commit_sha must be a lowercase full Git SHA")
        if _SHA256_PATTERN.fullmatch(self.content_sha256) is None:
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class AgentDeployment:
    """Runtime and policy profile pinned to one instruction revision."""

    deployment_key: str
    agent_key: AgentKey
    instruction: InstructionRevision
    runtime_profile: str
    model_profile: str
    policy_version: str
    state: AgentDeploymentState = AgentDeploymentState.STAGED

    def __post_init__(self) -> None:
        _require_token(self.deployment_key, field_name="deployment_key", maximum=128)
        _require_token(self.runtime_profile, field_name="runtime_profile", maximum=128)
        _require_token(self.model_profile, field_name="model_profile", maximum=128)
        _require_token(self.policy_version, field_name="policy_version", maximum=128)


@dataclass(frozen=True, slots=True)
class AgentSession:
    """One dialog-scoped session pinned to a deployment revision."""

    session_key: str
    workspace_id: str
    dialog_id: str = field(repr=False)
    deployment: AgentDeployment

    def __post_init__(self) -> None:
        _require_token(self.session_key, field_name="session_key", maximum=128)
        _require_token(self.workspace_id, field_name="workspace_id", maximum=128)
        _require_token(self.dialog_id, field_name="dialog_id", maximum=128)


@dataclass(frozen=True, slots=True)
class AgentContextMessage:
    """Bounded dialog-local context kept separate from system instructions."""

    message_id: str = field(repr=False)
    role: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.message_id, field_name="message_id", maximum=128)
        if self.role not in {"user", "assistant"}:
            raise ValueError("context role must be user or assistant")
        _require_text(self.content, field_name="content", maximum=16_000)


@dataclass(frozen=True, slots=True)
class AgentTurnCommand:
    """One idempotent, delivery-free proposal request."""

    turn_key: str
    session: AgentSession
    trigger_message_id: str = field(repr=False)
    user_text: str = field(repr=False)
    context: tuple[AgentContextMessage, ...] = ()

    def __post_init__(self) -> None:
        _require_token(self.turn_key, field_name="turn_key", maximum=128)
        _require_token(
            self.trigger_message_id,
            field_name="trigger_message_id",
            maximum=128,
        )
        _require_text(self.user_text, field_name="user_text", maximum=16_000)
        if len(self.context) > 32:
            raise ValueError("context may contain at most 32 messages")
        if len({item.message_id for item in self.context}) != len(self.context):
            raise ValueError("context message identities must be unique")


@dataclass(frozen=True, slots=True)
class AgentRuntimeResult:
    """Structured proposal that has no target and grants no delivery authority."""

    kind: AgentResultKind
    result_code: str
    candidate_text: str | None = field(default=None, repr=False)
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        _require_token(self.result_code, field_name="result_code", maximum=128)
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("runtime token usage cannot be negative")
        if self.kind is AgentResultKind.REPLY:
            if self.candidate_text is None:
                raise ValueError("reply result requires candidate_text")
            _require_text(self.candidate_text, field_name="candidate_text", maximum=8_000)
        elif self.candidate_text is not None:
            raise ValueError("handoff and no_reply results cannot carry candidate_text")


class AgentRuntimePort(Protocol):
    """Model boundary: produces a proposal but cannot select a target or send."""

    async def propose(self, command: AgentTurnCommand) -> AgentRuntimeResult:
        """Return one schema-validated delivery-free result."""


def _require_token(value: str, *, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded non-empty token")


def _require_text(value: str, *, field_name: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be bounded non-empty text")
