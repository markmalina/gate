"""Validated deployed instruction bundles and a network-free shadow runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from app.application.agent_kernel import (
    AgentResultKind,
    AgentRuntimePort,
    AgentRuntimeResult,
    AgentTurnCommand,
    InstructionRevision,
)
from app.application.work_ownership import AgentKey

_BUNDLE_SCHEMA_VERSION = "agent-bundle-v1"
_MAX_FILE_BYTES = 256_000
_ALLOWED_CAPABILITIES = frozenset({"reply", "handoff", "no_reply"})


class InstructionBundleError(ValueError):
    """A bounded deployed artifact validation failure."""


@dataclass(frozen=True, slots=True)
class EvaluationFixture:
    """One explicit offline input/output case used only by the shadow runtime."""

    input_text: str = field(repr=False)
    result: AgentRuntimeResult


@dataclass(frozen=True, slots=True)
class LoadedInstructionBundle:
    """Verified instruction material loaded from the deployed artifact."""

    agent_key: AgentKey
    revision: InstructionRevision
    system_instructions: str = field(repr=False)
    allowed_capabilities: frozenset[str]
    evaluations: tuple[EvaluationFixture, ...] = field(repr=False)


class DeployedInstructionBundleLoader:
    """Load immutable bundle files without Git credentials or network access."""

    def __init__(self, artifact_root: Path) -> None:
        self._artifact_root = artifact_root.resolve()

    def load(self, relative_path: str) -> LoadedInstructionBundle:
        bundle_dir = _safe_child(self._artifact_root, relative_path)
        manifest = _json_object(_read_bounded(bundle_dir / "manifest.json"), "manifest")
        if manifest.get("schema_version") != _BUNDLE_SCHEMA_VERSION:
            raise InstructionBundleError("bundle_schema_version_invalid")

        system_bytes = _read_bounded(bundle_dir / "system.md")
        policy_bytes = _read_bounded(bundle_dir / "policy.json")
        evaluation_bytes = _read_bounded(bundle_dir / "evaluations.json")
        expected_digest = manifest.get("content_sha256")
        actual_digest = _bundle_digest(system_bytes, policy_bytes, evaluation_bytes)
        if not isinstance(expected_digest, str) or expected_digest != actual_digest:
            raise InstructionBundleError("bundle_content_digest_mismatch")

        policy = _json_object(policy_bytes, "policy")
        capabilities = policy.get("allowed_capabilities")
        if not isinstance(capabilities, list) or not all(
            isinstance(value, str) for value in capabilities
        ):
            raise InstructionBundleError("bundle_capabilities_invalid")
        allowed_capabilities = frozenset(cast(list[str], capabilities))
        if not allowed_capabilities or not allowed_capabilities <= _ALLOWED_CAPABILITIES:
            raise InstructionBundleError("bundle_capabilities_invalid")

        try:
            agent_key = AgentKey(str(manifest["agent_key"]))
            revision = InstructionRevision(
                revision_key=str(manifest["revision_key"]),
                source_repository=str(manifest["source_repository"]),
                source_path=str(manifest["source_path"]),
                source_commit_sha=str(manifest["source_commit_sha"]),
                content_sha256=actual_digest,
                schema_version=_BUNDLE_SCHEMA_VERSION,
            )
            system_instructions = system_bytes.decode("utf-8")
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            raise InstructionBundleError("bundle_manifest_invalid") from exc
        if not system_instructions.strip():
            raise InstructionBundleError("bundle_system_instructions_empty")

        evaluations = _evaluation_fixtures(evaluation_bytes)
        return LoadedInstructionBundle(
            agent_key=agent_key,
            revision=revision,
            system_instructions=system_instructions,
            allowed_capabilities=allowed_capabilities,
            evaluations=evaluations,
        )


class EvaluationFixtureAgentRuntime(AgentRuntimePort):
    """Generate traceable proposals only for reviewed offline fixtures."""

    def __init__(self, bundle: LoadedInstructionBundle) -> None:
        self._bundle = bundle

    async def propose(self, command: AgentTurnCommand) -> AgentRuntimeResult:
        deployment = command.session.deployment
        if deployment.agent_key is not self._bundle.agent_key:
            raise InstructionBundleError("bundle_agent_mismatch")
        if deployment.instruction != self._bundle.revision:
            raise InstructionBundleError("bundle_revision_mismatch")
        for fixture in self._bundle.evaluations:
            if fixture.input_text == command.user_text:
                return fixture.result
        return AgentRuntimeResult(
            kind=AgentResultKind.NO_REPLY,
            result_code="fixture_not_found",
        )


def _safe_child(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise InstructionBundleError("bundle_path_invalid")
    candidate = (root / relative_path).resolve()
    if root not in candidate.parents:
        raise InstructionBundleError("bundle_path_invalid")
    return candidate


def _read_bounded(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        if size < 1 or size > _MAX_FILE_BYTES:
            raise InstructionBundleError("bundle_file_size_invalid")
        return path.read_bytes()
    except OSError as exc:
        raise InstructionBundleError("bundle_file_unavailable") from exc


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstructionBundleError(f"bundle_{label}_invalid") from exc
    if not isinstance(value, dict):
        raise InstructionBundleError(f"bundle_{label}_invalid")
    return cast(dict[str, Any], value)


def _bundle_digest(system: bytes, policy: bytes, evaluations: bytes) -> str:
    digest = hashlib.sha256()
    for label, content in (
        (b"system.md", system),
        (b"policy.json", policy),
        (b"evaluations.json", evaluations),
    ):
        digest.update(len(label).to_bytes(4, "big"))
        digest.update(label)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _evaluation_fixtures(raw: bytes) -> tuple[EvaluationFixture, ...]:
    parsed = _json_object(raw, "evaluations")
    cases = parsed.get("cases")
    if not isinstance(cases, list) or len(cases) > 100:
        raise InstructionBundleError("bundle_evaluations_invalid")
    fixtures: list[EvaluationFixture] = []
    seen_inputs: set[str] = set()
    for item in cases:
        if not isinstance(item, Mapping):
            raise InstructionBundleError("bundle_evaluations_invalid")
        input_text = item.get("input")
        kind_value = item.get("kind")
        result_code = item.get("result_code")
        candidate_text = item.get("candidate_text")
        if not isinstance(input_text, str) or input_text in seen_inputs:
            raise InstructionBundleError("bundle_evaluations_invalid")
        try:
            result = AgentRuntimeResult(
                kind=AgentResultKind(str(kind_value)),
                result_code=str(result_code),
                candidate_text=candidate_text if isinstance(candidate_text, str) else None,
            )
        except ValueError as exc:
            raise InstructionBundleError("bundle_evaluations_invalid") from exc
        fixtures.append(EvaluationFixture(input_text=input_text, result=result))
        seen_inputs.add(input_text)
    return tuple(fixtures)
