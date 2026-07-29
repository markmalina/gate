"""Delivery-free orchestration for one explicit Alpha group invocation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.application.agent_kernel import (
    AgentContextMessage,
    AgentDeployment,
    AgentDeploymentState,
    AgentResultKind,
    AgentSession,
    AgentTurnCommand,
)
from app.application.agent_shadow import (
    AgentShadowProposal,
    RunAgentShadowTurnService,
)
from app.application.alpha_group import (
    AlphaInstallation,
    AlphaInvocationFacts,
    AlphaInvocationKind,
    evaluate_alpha_invocation,
)
from app.application.alpha_lifecycle import AlphaUsageReservation
from app.application.instruction_bundles import (
    DeployedInstructionBundleLoader,
    EvaluationFixtureAgentRuntime,
    InstructionBundleError,
    LoadedInstructionBundle,
)

_ALPHA_COMMAND = "alpha"
ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD = 50_000_000
ALPHA_OPENAI_MAX_COST_MICRO_USD_PER_TOKEN = 6
_MODEL_RUNTIME_PROFILES = frozenset({"openai-responses-v1"})
ALPHA_BOUNDED_LIVE_DEPLOYMENT_KEY = "alpha-model-v2-openai"
ALPHA_BOUNDED_LIVE_POLICY_VERSION = "alpha-model-v2"
_FIXTURE_RUNTIME_PROFILES = frozenset(
    {"fixture", "fixture-only-v1", "evaluation-fixture-v1"}
)


class AlphaProposalDeliveryMode(StrEnum):
    """Whether a proposal may cross the separate Telegram delivery boundary."""

    SHADOW_ONLY = "shadow_only"
    CANARY = "canary"
    BOUNDED_LIVE = "bounded_live"


class AlphaShadowDisposition(StrEnum):
    """Closed observable outcomes that never imply delivery authority."""

    NOT_INVOKED = "not_invoked"
    BLOCKED = "blocked"
    PROPOSED = "proposed"
    REPLAYED = "replayed"
    FAILED = "failed"


class AlphaTurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PROPOSED = "proposed"
    FAILED = "failed"


class AlphaShadowWorkerError(RuntimeError):
    """A redacted orchestration refusal safe for operational reporting."""

    def __init__(self, code: str) -> None:
        self.code = code.strip()[:128] or "alpha_shadow_error"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ProcessAlphaShadowMessageCommand:
    workspace_key: str
    trigger_message_id: str = field(repr=False)
    bot_username: str
    now: datetime
    invocation_command: str = _ALPHA_COMMAND

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_token(self.trigger_message_id, "trigger_message_id", 128)
        _require_token(self.bot_username, "bot_username", 64)
        _require_token(self.invocation_command, "invocation_command", 32)
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.invocation_command):
            raise ValueError("invocation_command must be a Telegram command")
        if self.bot_username.startswith("@"):
            raise ValueError("bot_username must not include @")
        if self.now.tzinfo is None:
            raise ValueError("now must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AlphaShadowPreparedTurn:
    """DB-authoritative binding plus bounded, dialog-local untrusted content."""

    workspace_key: str
    installation: AlphaInstallation
    deployment: AgentDeployment
    instruction_bundle_path: str
    trigger_message_id: str = field(repr=False)
    user_text: str = field(repr=False)
    sender_is_human: bool
    reply_to_bot: bool
    context: tuple[AgentContextMessage, ...] = field(default=(), repr=False)
    max_tokens_per_turn: int = 2048

    def __post_init__(self) -> None:
        _require_token(self.workspace_key, "workspace_key", 128)
        _require_token(self.instruction_bundle_path, "instruction_bundle_path", 512)
        _require_token(self.trigger_message_id, "trigger_message_id", 128)
        if not self.user_text.strip() or len(self.user_text) > 16_000:
            raise ValueError("user_text must be bounded non-empty text")
        if len(self.context) > 32:
            raise ValueError("Alpha context may contain at most 32 messages")
        if self.max_tokens_per_turn < 1:
            raise ValueError("max_tokens_per_turn must be positive")


@dataclass(frozen=True, slots=True)
class AlphaTurnSnapshot:
    turn_key: str
    session_key: str
    status: AlphaTurnStatus
    result_kind: AgentResultKind | None = None
    result_code: str | None = None
    candidate_sha256: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AlphaShadowWorkerResult:
    disposition: AlphaShadowDisposition
    code: str
    turn_key: str | None = None
    result_kind: AgentResultKind | None = None
    candidate_sha256: str | None = field(default=None, repr=False)
    delivery_mode: AlphaProposalDeliveryMode = AlphaProposalDeliveryMode.SHADOW_ONLY


class AlphaShadowTurnPort(Protocol):
    """Durable phases around a runtime call; implementations own transactions."""

    async def prepare(
        self, command: ProcessAlphaShadowMessageCommand
    ) -> AlphaShadowPreparedTurn: ...

    async def reserve_usage(
        self,
        prepared: AlphaShadowPreparedTurn,
        *,
        now: datetime,
    ) -> AlphaUsageReservation: ...

    async def reserve_turn(
        self,
        prepared: AlphaShadowPreparedTurn,
    ) -> AlphaTurnSnapshot: ...

    async def claim_turn(self, turn_key: str) -> bool: ...

    async def complete_turn(
        self,
        *,
        workspace_key: str,
        turn_key: str,
        usage_key: str,
        proposal: AgentShadowProposal,
    ) -> AlphaTurnSnapshot: ...

    async def fail_turn(
        self,
        *,
        workspace_key: str,
        turn_key: str,
        usage_key: str,
        error_code: str,
    ) -> AlphaTurnSnapshot: ...

    async def get_turn(self, turn_key: str) -> AlphaTurnSnapshot: ...


class AlphaRuntimeFactory(Protocol):
    def build(
        self,
        *,
        bundle: LoadedInstructionBundle,
        deployment: AgentDeployment,
    ) -> RunAgentShadowTurnService: ...


class FixtureOnlyAlphaRuntimeFactory:
    """The only approved PR-B runtime is the network-free evaluation fixture."""

    _PROFILES = _FIXTURE_RUNTIME_PROFILES

    def build(
        self,
        *,
        bundle: LoadedInstructionBundle,
        deployment: AgentDeployment,
    ) -> RunAgentShadowTurnService:
        if deployment.runtime_profile not in self._PROFILES:
            raise AlphaShadowWorkerError("alpha_runtime_profile_not_approved")
        return RunAgentShadowTurnService(EvaluationFixtureAgentRuntime(bundle))


class ProcessAlphaShadowMessageService:
    """Run at most one delivery-free proposal for one canonical trigger."""

    def __init__(
        self,
        *,
        port: AlphaShadowTurnPort,
        bundle_loader: DeployedInstructionBundleLoader,
        runtime_factory: AlphaRuntimeFactory | None = None,
    ) -> None:
        self._port = port
        self._bundle_loader = bundle_loader
        self._runtime_factory = runtime_factory or FixtureOnlyAlphaRuntimeFactory()

    async def execute(
        self, command: ProcessAlphaShadowMessageCommand
    ) -> AlphaShadowWorkerResult:
        prepared = await self._port.prepare(command)
        delivery_mode = alpha_proposal_delivery_mode(prepared.deployment)
        invocation_kind = recognize_alpha_invocation(
            prepared.user_text,
            bot_username=command.bot_username,
            reply_to_bot=prepared.reply_to_bot,
            command_name=command.invocation_command,
        )
        decision = evaluate_alpha_invocation(
            AlphaInvocationFacts(
                connector_enabled=True,
                installation=prepared.installation,
                sender_is_human=prepared.sender_is_human,
                invocation_kind=invocation_kind,
                turns_in_window=0,
                max_turns_per_window=1,
            )
        )
        if not decision.allowed:
            disposition = (
                AlphaShadowDisposition.NOT_INVOKED
                if invocation_kind is AlphaInvocationKind.NONE
                else AlphaShadowDisposition.BLOCKED
            )
            return AlphaShadowWorkerResult(disposition, decision.reason.value)

        usage = await self._port.reserve_usage(prepared, now=command.now)
        if not usage.allowed or usage.usage_key is None:
            return AlphaShadowWorkerResult(
                AlphaShadowDisposition.BLOCKED,
                usage.reason.value,
            )

        turn = await self._port.reserve_turn(prepared)
        replay = await self._replay_if_terminal(
            turn,
            workspace_key=command.workspace_key,
            usage_key=usage.usage_key,
            delivery_mode=delivery_mode,
        )
        if replay is not None:
            return replay

        try:
            bundle = self._bundle_loader.load(prepared.instruction_bundle_path)
            _validate_bundle(bundle=bundle, deployment=prepared.deployment)
            runner = self._runtime_factory.build(
                bundle=bundle,
                deployment=prepared.deployment,
            )
        except (InstructionBundleError, AlphaShadowWorkerError, ValueError) as exc:
            code = (
                exc.code
                if isinstance(exc, AlphaShadowWorkerError)
                else str(exc).strip()[:128] or "alpha_bundle_invalid"
            )
            failed = await self._port.fail_turn(
                workspace_key=command.workspace_key,
                turn_key=turn.turn_key,
                usage_key=usage.usage_key,
                error_code=code,
            )
            return _failed_result(failed)

        claimed = await self._port.claim_turn(turn.turn_key)
        if not claimed:
            current = await self._port.get_turn(turn.turn_key)
            replay = await self._replay_if_terminal(
                current,
                workspace_key=command.workspace_key,
                usage_key=usage.usage_key,
                delivery_mode=delivery_mode,
            )
            if replay is not None:
                return replay
            return AlphaShadowWorkerResult(
                AlphaShadowDisposition.BLOCKED,
                "alpha_turn_concurrently_running",
                turn_key=current.turn_key,
            )

        session = AgentSession(
            session_key=turn.session_key,
            workspace_id=prepared.installation.workspace_id,
            dialog_id=prepared.installation.dialog_id,
            deployment=prepared.deployment,
        )
        agent_command = AgentTurnCommand(
            turn_key=turn.turn_key,
            session=session,
            trigger_message_id=prepared.trigger_message_id,
            user_text=prepared.user_text,
            context=prepared.context,
        )
        try:
            proposal = await runner.execute(agent_command)
            if (
                proposal.input_tokens + proposal.output_tokens
                > prepared.max_tokens_per_turn
            ):
                raise AlphaShadowWorkerError("alpha_runtime_token_limit_exceeded")
            completed = await self._port.complete_turn(
                workspace_key=command.workspace_key,
                turn_key=turn.turn_key,
                usage_key=usage.usage_key,
                proposal=proposal,
            )
        except AlphaShadowWorkerError as exc:
            failed = await self._port.fail_turn(
                workspace_key=command.workspace_key,
                turn_key=turn.turn_key,
                usage_key=usage.usage_key,
                error_code=exc.code,
            )
            return _failed_result(failed)
        except Exception:
            failed = await self._port.fail_turn(
                workspace_key=command.workspace_key,
                turn_key=turn.turn_key,
                usage_key=usage.usage_key,
                error_code="alpha_runtime_failed",
            )
            return _failed_result(failed)

        return AlphaShadowWorkerResult(
            AlphaShadowDisposition.PROPOSED,
            completed.result_code or "alpha_proposed",
            turn_key=completed.turn_key,
            result_kind=completed.result_kind,
            candidate_sha256=completed.candidate_sha256,
            delivery_mode=delivery_mode,
        )

    async def _replay_if_terminal(
        self,
        turn: AlphaTurnSnapshot,
        *,
        workspace_key: str,
        usage_key: str,
        delivery_mode: AlphaProposalDeliveryMode,
    ) -> AlphaShadowWorkerResult | None:
        if turn.status is AlphaTurnStatus.PROPOSED:
            return AlphaShadowWorkerResult(
                AlphaShadowDisposition.REPLAYED,
                turn.result_code or "alpha_proposed",
                turn_key=turn.turn_key,
                result_kind=turn.result_kind,
                candidate_sha256=turn.candidate_sha256,
                delivery_mode=delivery_mode,
            )
        if turn.status is AlphaTurnStatus.FAILED:
            return _failed_result(turn, replayed=True)
        if turn.status is AlphaTurnStatus.RUNNING:
            failed = await self._port.fail_turn(
                workspace_key=workspace_key,
                turn_key=turn.turn_key,
                usage_key=usage_key,
                error_code="alpha_runtime_outcome_unknown",
            )
            return _failed_result(failed)
        return None


def alpha_proposal_delivery_mode(
    deployment: AgentDeployment,
) -> AlphaProposalDeliveryMode:
    """Return explicit delivery authority; model runtimes fail closed by default."""

    if (
        deployment.runtime_profile in _FIXTURE_RUNTIME_PROFILES
        and deployment.model_profile in {"none", "no-network"}
    ):
        return AlphaProposalDeliveryMode.CANARY
    if (
        deployment.deployment_key == ALPHA_BOUNDED_LIVE_DEPLOYMENT_KEY
        and deployment.runtime_profile in _MODEL_RUNTIME_PROFILES
        and deployment.policy_version == ALPHA_BOUNDED_LIVE_POLICY_VERSION
        and deployment.state is AgentDeploymentState.ACTIVE
    ):
        return AlphaProposalDeliveryMode.BOUNDED_LIVE
    return AlphaProposalDeliveryMode.SHADOW_ONLY


def alpha_model_usage_budget(
    deployment: AgentDeployment,
) -> tuple[int, int] | None:
    """Return the hard UTC-month budget and conservative unit cost for model runtimes."""

    if deployment.runtime_profile not in _MODEL_RUNTIME_PROFILES:
        return None
    return (
        ALPHA_MODEL_MONTHLY_BUDGET_MICRO_USD,
        ALPHA_OPENAI_MAX_COST_MICRO_USD_PER_TOKEN,
    )


def recognize_alpha_invocation(
    text: str,
    *,
    bot_username: str,
    reply_to_bot: bool,
    command_name: str = _ALPHA_COMMAND,
) -> AlphaInvocationKind:
    """Recognize one targeted command, exact mention, or verified reply."""

    if reply_to_bot:
        return AlphaInvocationKind.REPLY_TO_BOT
    stripped = text.lstrip()
    command = re.match(
        r"^/(?P<command>[A-Za-z0-9_]+)(?:@(?P<target>[A-Za-z0-9_]{5,64}))?(?=\s|$)",
        stripped,
    )
    if command is not None and command.group("command").casefold() == command_name.casefold():
        target = command.group("target")
        if target is not None and target.casefold() == bot_username.casefold():
            return AlphaInvocationKind.COMMAND
    mention = re.compile(
        rf"(?<![A-Za-z0-9_])@{re.escape(bot_username)}(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    if mention.search(text) is not None:
        return AlphaInvocationKind.MENTION
    return AlphaInvocationKind.NONE


def _validate_bundle(
    *,
    bundle: LoadedInstructionBundle,
    deployment: AgentDeployment,
) -> None:
    if bundle.agent_key is not deployment.agent_key:
        raise AlphaShadowWorkerError("alpha_bundle_agent_mismatch")
    if bundle.revision != deployment.instruction:
        raise AlphaShadowWorkerError("alpha_bundle_revision_mismatch")
    if not bundle.allowed_capabilities <= {"reply", "handoff", "no_reply"}:
        raise AlphaShadowWorkerError("alpha_bundle_capability_mismatch")


def _failed_result(
    turn: AlphaTurnSnapshot,
    *,
    replayed: bool = False,
) -> AlphaShadowWorkerResult:
    return AlphaShadowWorkerResult(
        AlphaShadowDisposition.REPLAYED if replayed else AlphaShadowDisposition.FAILED,
        turn.result_code or "alpha_turn_failed",
        turn_key=turn.turn_key,
    )


def _require_token(value: str, field_name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a bounded token")
