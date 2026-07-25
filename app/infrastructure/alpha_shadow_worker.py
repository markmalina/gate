"""Transactional PostgreSQL phases for the delivery-free Alpha shadow worker."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.application.agent_kernel import (
    AgentContextMessage,
    AgentDeployment,
    AgentDeploymentState,
    AgentResultKind,
    InstructionRevision,
)
from app.application.agent_shadow import AgentShadowProposal
from app.application.alpha_delivery import MAX_TELEGRAM_TEXT_CHARS
from app.application.alpha_group import (
    AlphaInstallation,
    AlphaInstallationState,
    AlphaInvocationKind,
)
from app.application.alpha_lifecycle import (
    AlphaUsageReservation,
    RecordAlphaUsageCommand,
    ReserveAlphaUsageCommand,
)
from app.application.alpha_shadow_worker import (
    ALPHA_BOUNDED_LIVE_DEPLOYMENT_KEY,
    ALPHA_BOUNDED_LIVE_POLICY_VERSION,
    AlphaShadowPreparedTurn,
    AlphaShadowTurnPort,
    AlphaShadowWorkerError,
    AlphaTurnSnapshot,
    AlphaTurnStatus,
    ProcessAlphaShadowMessageCommand,
    alpha_model_usage_budget,
    recognize_alpha_invocation,
)
from app.application.canonical_messaging import DialogKind
from app.application.work_ownership import AgentKey
from app.infrastructure.alpha_lifecycle import SqlAlphaLifecycleRepository
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    AgentSessionModel,
    AgentTurnModel,
    InstructionRevisionModel,
)
from app.models.alpha import (
    AgentInstallationModel,
    AgentSendAttemptModel,
    DeliveryGrantModel,
)
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace
from app.models.domain import TelegramReceipt

_OPENAI_RUNTIME_PROFILE = "openai-responses-v1"
_OPENAI_MODEL_PROFILE = "gpt-5.6-luna"


class SessionFactoryAlphaShadowTurnRepository(AlphaShadowTurnPort):
    """Keep runtime outside transactions while making every phase durable."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        verified_model_shadow_enabled: bool = False,
    ) -> None:
        self._sessions = sessions
        self._verified_model_shadow_enabled = verified_model_shadow_enabled

    async def prepare(
        self, command: ProcessAlphaShadowMessageCommand
    ) -> AlphaShadowPreparedTurn:
        trigger_id = _uuid(command.trigger_message_id, "trigger_message_id")
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(
                        Workspace,
                        TransportConnector,
                        Dialog,
                        CanonicalMessage,
                        TelegramReceipt,
                        AgentInstallationModel,
                        AgentDeploymentModel,
                        InstructionRevisionModel,
                        AgentDefinitionModel,
                    )
                    .join(
                        TransportConnector,
                        TransportConnector.workspace_id == Workspace.id,
                    )
                    .join(
                        Dialog,
                        (Dialog.workspace_id == Workspace.id)
                        & (Dialog.connector_id == TransportConnector.id),
                    )
                    .join(
                        CanonicalMessage,
                        (CanonicalMessage.workspace_id == Workspace.id)
                        & (CanonicalMessage.dialog_id == Dialog.id),
                    )
                    .join(
                        TelegramReceipt,
                        TelegramReceipt.id == CanonicalMessage.receipt_id,
                    )
                    .join(
                        AgentInstallationModel,
                        (AgentInstallationModel.workspace_id == Workspace.id)
                        & (AgentInstallationModel.connector_id == TransportConnector.id)
                        & (AgentInstallationModel.dialog_id == Dialog.id),
                    )
                    .join(
                        AgentDeploymentModel,
                        (AgentDeploymentModel.workspace_id == Workspace.id)
                        & (
                            AgentDeploymentModel.id
                            == AgentInstallationModel.deployment_id
                        ),
                    )
                    .join(
                        InstructionRevisionModel,
                        (InstructionRevisionModel.workspace_id == Workspace.id)
                        & (
                            InstructionRevisionModel.id
                            == AgentInstallationModel.instruction_revision_id
                        )
                        & (
                            InstructionRevisionModel.id
                            == AgentDeploymentModel.instruction_revision_id
                        ),
                    )
                    .join(
                        AgentDefinitionModel,
                        (AgentDefinitionModel.workspace_id == Workspace.id)
                        & (
                            AgentDefinitionModel.id
                            == AgentDeploymentModel.agent_definition_id
                        ),
                    )
                    .where(
                        Workspace.key == command.workspace_key,
                        Workspace.is_active.is_(True),
                        TransportConnector.connector_kind == "telegram_bot",
                        TransportConnector.is_enabled.is_(True),
                        Dialog.dialog_kind.in_(
                            {DialogKind.GROUP.value, DialogKind.SUPERGROUP.value}
                        ),
                        CanonicalMessage.id == trigger_id,
                        CanonicalMessage.direction == "inbound",
                        AgentInstallationModel.state
                        == AlphaInstallationState.ACTIVE.value,
                        AgentDeploymentModel.state
                        == AgentDeploymentState.ACTIVE.value,
                        InstructionRevisionModel.validation_state == "validated",
                        AgentDefinitionModel.agent_key == AgentKey.ALPHA_GROUP.value,
                    )
                )
            ).one_or_none()
            if row is None:
                raise AlphaShadowWorkerError("alpha_trigger_not_eligible")

            (
                workspace,
                connector,
                dialog,
                trigger,
                receipt,
                installation,
                deployment,
                revision,
                definition,
            ) = row
            installation_deployment = deployment
            installation_revision = revision
            if connector.connector_key.strip() == "":
                raise AlphaShadowWorkerError("alpha_connector_identity_invalid")
            _require_trigger_connector(
                receipt=receipt,
                connector_key=connector.connector_key,
            )
            message = _verified_trigger_message(
                receipt.payload,
                canonical=trigger,
                dialog=dialog,
            )
            text = message.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 16_000:
                raise AlphaShadowWorkerError("alpha_trigger_text_invalid")
            sender_is_human = _sender_is_human(message)
            reply_to_bot = _is_reply_to_bot(
                message,
                bot_username=command.bot_username,
            )
            if self._verified_model_shadow_enabled:
                model_binding = await _verified_model_shadow_binding(
                    session,
                    workspace_id=workspace.id,
                    definition_id=definition.id,
                )
                if model_binding is not None:
                    deployment, revision = model_binding

            revision_value = InstructionRevision(
                revision_key=revision.revision_key,
                source_repository=revision.source_repository,
                source_path=revision.source_path,
                source_commit_sha=revision.source_commit_sha,
                content_sha256=revision.content_sha256,
                schema_version=revision.schema_version,
            )
            deployment_value = AgentDeployment(
                deployment_key=deployment.deployment_key,
                agent_key=AgentKey.ALPHA_GROUP,
                instruction=revision_value,
                runtime_profile=deployment.runtime_profile,
                model_profile=deployment.model_profile,
                policy_version=deployment.policy_version,
                state=AgentDeploymentState(deployment.state),
            )
            installation_value = AlphaInstallation(
                installation_key=installation.installation_key,
                workspace_id=str(workspace.id),
                dialog_id=str(dialog.id),
                dialog_kind=DialogKind(dialog.dialog_kind),
                deployment_key=installation_deployment.deployment_key,
                instruction_content_sha256=installation_revision.content_sha256,
                state=AlphaInstallationState(installation.state),
            )
            return AlphaShadowPreparedTurn(
                workspace_key=workspace.key,
                installation=installation_value,
                deployment=deployment_value,
                instruction_bundle_path=revision.source_path,
                trigger_message_id=str(trigger.id),
                user_text=text,
                sender_is_human=sender_is_human,
                reply_to_bot=reply_to_bot,
                # Keep AlphaViaBot turns independent. Historical dialog context
                # belongs to future explicitly stateful products, not this bot.
                context=(),
                max_tokens_per_turn=installation.max_tokens_per_turn,
            )

    async def reserve_usage(
        self,
        prepared: AlphaShadowPreparedTurn,
        *,
        now: datetime,
    ) -> AlphaUsageReservation:
        budget = alpha_model_usage_budget(prepared.deployment)
        async with self._sessions.begin() as session:
            return await SqlAlphaLifecycleRepository(session).reserve_usage(
                ReserveAlphaUsageCommand(
                    workspace_key=prepared.workspace_key,
                    installation_key=prepared.installation.installation_key,
                    trigger_message_id=prepared.trigger_message_id,
                    reserved_tokens=prepared.max_tokens_per_turn,
                    now=now,
                    monthly_budget_microusd=(budget[0] if budget is not None else None),
                    max_cost_microusd_per_token=(budget[1] if budget is not None else 0),
                )
            )

    async def reserve_turn(
        self,
        prepared: AlphaShadowPreparedTurn,
    ) -> AlphaTurnSnapshot:
        workspace_id = _uuid(prepared.installation.workspace_id, "workspace_id")
        dialog_id = _uuid(prepared.installation.dialog_id, "dialog_id")
        trigger_id = _uuid(prepared.trigger_message_id, "trigger_message_id")
        session_key = _stable_key(
            "alpha-session",
            prepared.installation.workspace_id,
            prepared.installation.installation_key,
            prepared.deployment.deployment_key,
        )
        context_ids = [item.message_id for item in prepared.context]
        async with self._sessions.begin() as session:
            binding = (
                await session.execute(
                    select(AgentDeploymentModel.id, InstructionRevisionModel.id)
                    .join(
                        InstructionRevisionModel,
                        InstructionRevisionModel.id
                        == AgentDeploymentModel.instruction_revision_id,
                    )
                    .where(
                        AgentDeploymentModel.workspace_id == workspace_id,
                        AgentDeploymentModel.deployment_key
                        == prepared.deployment.deployment_key,
                        AgentDeploymentModel.state == prepared.deployment.state.value,
                        InstructionRevisionModel.workspace_id == workspace_id,
                        InstructionRevisionModel.revision_key
                        == prepared.deployment.instruction.revision_key,
                        InstructionRevisionModel.validation_state == "validated",
                    )
                )
            ).one_or_none()
            if binding is None:
                raise AlphaShadowWorkerError("alpha_turn_binding_invalid")
            deployment_id, revision_id = binding

            session_id = await session.scalar(
                insert(AgentSessionModel)
                .values(
                    workspace_id=workspace_id,
                    dialog_id=dialog_id,
                    deployment_id=deployment_id,
                    instruction_revision_id=revision_id,
                    session_key=session_key,
                    state="active",
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        AgentSessionModel.workspace_id,
                        AgentSessionModel.session_key,
                    ]
                )
                .returning(AgentSessionModel.id)
            )
            if session_id is None:
                existing_session = await session.scalar(
                    select(AgentSessionModel).where(
                        AgentSessionModel.workspace_id == workspace_id,
                        AgentSessionModel.session_key == session_key,
                    )
                )
                if (
                    existing_session is None
                    or existing_session.dialog_id != dialog_id
                    or existing_session.deployment_id != deployment_id
                    or existing_session.instruction_revision_id != revision_id
                ):
                    raise AlphaShadowWorkerError("alpha_session_binding_conflict")
                session_id = existing_session.id

            turn_id = await session.scalar(
                insert(AgentTurnModel)
                .values(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    trigger_message_id=trigger_id,
                    instruction_revision_id=revision_id,
                    context_message_ids=context_ids,
                    status=AlphaTurnStatus.QUEUED.value,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        AgentTurnModel.session_id,
                        AgentTurnModel.trigger_message_id,
                    ]
                )
                .returning(AgentTurnModel.id)
            )
            if turn_id is None:
                existing_turn = await session.scalar(
                    select(AgentTurnModel).where(
                        AgentTurnModel.session_id == session_id,
                        AgentTurnModel.trigger_message_id == trigger_id,
                    )
                )
                if existing_turn is None:
                    raise AlphaShadowWorkerError("alpha_turn_reservation_failed")
                return _snapshot(existing_turn, session_key=session_key)

            created = await session.get(AgentTurnModel, turn_id)
            if created is None:
                raise AlphaShadowWorkerError("alpha_turn_reservation_failed")
            return _snapshot(created, session_key=session_key)

    async def claim_turn(self, turn_key: str) -> bool:
        turn_id = _turn_id(turn_key)
        async with self._sessions.begin() as session:
            claimed = await session.scalar(
                update(AgentTurnModel)
                .where(
                    AgentTurnModel.id == turn_id,
                    AgentTurnModel.status == AlphaTurnStatus.QUEUED.value,
                )
                .values(status=AlphaTurnStatus.RUNNING.value)
                .returning(AgentTurnModel.id)
            )
            return claimed is not None

    async def complete_turn(
        self,
        *,
        workspace_key: str,
        turn_key: str,
        usage_key: str,
        proposal: AgentShadowProposal,
    ) -> AlphaTurnSnapshot:
        turn_id = _turn_id(turn_key)
        async with self._sessions.begin() as session:
            turn, session_model, deployment, revision = await _locked_turn_binding(
                session,
                workspace_key=workspace_key,
                turn_id=turn_id,
            )
            if turn.status == AlphaTurnStatus.PROPOSED.value:
                expected = (
                    proposal.result_kind.value,
                    proposal.result_code,
                    proposal.candidate_text,
                    proposal.candidate_sha256,
                )
                current = (
                    turn.result_kind,
                    turn.result_code,
                    turn.candidate_text,
                    turn.candidate_sha256,
                )
                if current != expected:
                    raise AlphaShadowWorkerError("alpha_turn_completion_conflict")
                return _snapshot(turn, session_key=session_model.session_key)
            if turn.status != AlphaTurnStatus.RUNNING.value:
                raise AlphaShadowWorkerError("alpha_turn_not_running")
            if (
                proposal.turn_key != turn_key
                or proposal.deployment_key != deployment.deployment_key
                or proposal.instruction_commit_sha != revision.source_commit_sha
                or proposal.instruction_content_sha256 != revision.content_sha256
                or proposal.policy_version != deployment.policy_version
            ):
                raise AlphaShadowWorkerError("alpha_turn_trace_mismatch")

            await SqlAlphaLifecycleRepository(session).record_usage(
                RecordAlphaUsageCommand(
                    workspace_key=workspace_key,
                    usage_key=usage_key,
                    input_tokens=proposal.input_tokens,
                    output_tokens=proposal.output_tokens,
                )
            )
            turn.status = AlphaTurnStatus.PROPOSED.value
            turn.result_kind = proposal.result_kind.value
            turn.result_code = proposal.result_code
            turn.candidate_text = proposal.candidate_text
            turn.candidate_sha256 = proposal.candidate_sha256
            await session.flush()
            return _snapshot(turn, session_key=session_model.session_key)

    async def fail_turn(
        self,
        *,
        workspace_key: str,
        turn_key: str,
        usage_key: str,
        error_code: str,
    ) -> AlphaTurnSnapshot:
        turn_id = _turn_id(turn_key)
        code = error_code.strip()[:128] or "alpha_turn_failed"
        async with self._sessions.begin() as session:
            turn, session_model, _deployment, _revision = await _locked_turn_binding(
                session,
                workspace_key=workspace_key,
                turn_id=turn_id,
            )
            if turn.status == AlphaTurnStatus.PROPOSED.value:
                raise AlphaShadowWorkerError("alpha_proposed_turn_cannot_fail")
            if turn.status != AlphaTurnStatus.FAILED.value:
                turn.status = AlphaTurnStatus.FAILED.value
                turn.result_kind = None
                turn.result_code = code
                turn.candidate_text = None
                turn.candidate_sha256 = None
            elif turn.result_code != code:
                raise AlphaShadowWorkerError("alpha_turn_failure_conflict")
            await SqlAlphaLifecycleRepository(session).record_usage(
                RecordAlphaUsageCommand(
                    workspace_key=workspace_key,
                    usage_key=usage_key,
                    input_tokens=0,
                    output_tokens=0,
                )
            )
            await session.flush()
            return _snapshot(turn, session_key=session_model.session_key)

    async def get_turn(self, turn_key: str) -> AlphaTurnSnapshot:
        turn_id = _turn_id(turn_key)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AgentTurnModel, AgentSessionModel)
                    .join(
                        AgentSessionModel,
                        AgentSessionModel.id == AgentTurnModel.session_id,
                    )
                    .where(AgentTurnModel.id == turn_id)
                )
            ).one_or_none()
            if row is None:
                raise AlphaShadowWorkerError("alpha_turn_not_found")
            return _snapshot(row[0], session_key=row[1].session_key)


async def _verified_model_shadow_binding(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    definition_id: uuid.UUID,
) -> tuple[AgentDeploymentModel, InstructionRevisionModel] | None:
    """Prefer one reviewed ACTIVE model, otherwise keep one STAGED shadow."""

    from app.models.alpha_preflight import AlphaModelPreflightModel

    rows = (
        await session.execute(
            select(AgentDeploymentModel, InstructionRevisionModel)
            .join(
                InstructionRevisionModel,
                (
                    InstructionRevisionModel.id
                    == AgentDeploymentModel.instruction_revision_id
                )
                & (
                    InstructionRevisionModel.workspace_id
                    == AgentDeploymentModel.workspace_id
                ),
            )
            .join(
                AlphaModelPreflightModel,
                (AlphaModelPreflightModel.workspace_id == workspace_id)
                & (
                    AlphaModelPreflightModel.deployment_id
                    == AgentDeploymentModel.id
                )
                & (
                    AlphaModelPreflightModel.instruction_revision_id
                    == InstructionRevisionModel.id
                ),
            )
            .where(
                AgentDeploymentModel.workspace_id == workspace_id,
                AgentDeploymentModel.agent_definition_id == definition_id,
                AgentDeploymentModel.runtime_profile == _OPENAI_RUNTIME_PROFILE,
                AgentDeploymentModel.model_profile == _OPENAI_MODEL_PROFILE,
                AgentDeploymentModel.state.in_(
                    (
                        AgentDeploymentState.STAGED.value,
                        AgentDeploymentState.ACTIVE.value,
                    )
                ),
                InstructionRevisionModel.validation_state == "validated",
                AlphaModelPreflightModel.state == "succeeded",
            )
        )
    ).all()
    active_rows = [
        row
        for row in rows
        if row[0].state == AgentDeploymentState.ACTIVE.value
        and row[0].deployment_key == ALPHA_BOUNDED_LIVE_DEPLOYMENT_KEY
        and row[0].policy_version == ALPHA_BOUNDED_LIVE_POLICY_VERSION
    ]
    if len(active_rows) > 1:
        raise AlphaShadowWorkerError("alpha_model_live_binding_ambiguous")
    if active_rows:
        return active_rows[0][0], active_rows[0][1]

    staged_rows = [
        row
        for row in rows
        if row[0].state == AgentDeploymentState.STAGED.value
    ]
    if len(staged_rows) > 1:
        raise AlphaShadowWorkerError("alpha_model_shadow_binding_ambiguous")
    if staged_rows:
        return staged_rows[0][0], staged_rows[0][1]
    return None


async def _locked_turn_binding(
    session: AsyncSession,
    *,
    workspace_key: str,
    turn_id: uuid.UUID,
) -> tuple[
    AgentTurnModel,
    AgentSessionModel,
    AgentDeploymentModel,
    InstructionRevisionModel,
]:
    row = (
        await session.execute(
            select(
                AgentTurnModel,
                AgentSessionModel,
                AgentDeploymentModel,
                InstructionRevisionModel,
            )
            .join(
                AgentSessionModel,
                AgentSessionModel.id == AgentTurnModel.session_id,
            )
            .join(
                AgentDeploymentModel,
                AgentDeploymentModel.id == AgentSessionModel.deployment_id,
            )
            .join(
                InstructionRevisionModel,
                InstructionRevisionModel.id
                == AgentSessionModel.instruction_revision_id,
            )
            .join(Workspace, Workspace.id == AgentTurnModel.workspace_id)
            .where(
                AgentTurnModel.id == turn_id,
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                AgentSessionModel.workspace_id == AgentTurnModel.workspace_id,
                AgentDeploymentModel.workspace_id == AgentTurnModel.workspace_id,
                InstructionRevisionModel.workspace_id == AgentTurnModel.workspace_id,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise AlphaShadowWorkerError("alpha_turn_not_found")
    return row[0], row[1], row[2], row[3]


async def _load_context(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    dialog: Dialog,
    trigger: CanonicalMessage,
    trigger_received_at: datetime,
    installation_id: uuid.UUID,
    connector_key: str,
    bot_username: str,
) -> tuple[AgentContextMessage, ...]:
    """Return verified dialog history, including only literally delivered Alpha replies.

    Valid historical deployment/revision replies survive an installation upgrade. Each
    historical session/deployment/revision chain is revalidated independently; current
    installation bindings are intentionally not imposed on earlier sent turns.
    """
    inbound_rows = (
        await session.execute(
            select(CanonicalMessage, TelegramReceipt)
            .join(TelegramReceipt, TelegramReceipt.id == CanonicalMessage.receipt_id)
            .where(
                CanonicalMessage.workspace_id == workspace_id,
                CanonicalMessage.dialog_id == dialog.id,
                CanonicalMessage.id != trigger.id,
                CanonicalMessage.direction == "inbound",
                TelegramReceipt.bot_key == connector_key,
                TelegramReceipt.received_at < trigger_received_at,
            )
            .order_by(
                TelegramReceipt.received_at.desc(),
                CanonicalMessage.id.desc(),
            )
            .limit(64)
        )
    ).all()
    events: list[tuple[datetime, int, str, AgentContextMessage]] = []
    for canonical, receipt in inbound_rows:
        if receipt.bot_key != connector_key:
            continue
        try:
            message = _verified_trigger_message(
                receipt.payload,
                canonical=canonical,
                dialog=dialog,
            )
        except AlphaShadowWorkerError:
            continue
        text = message.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 16_000:
            continue
        if not _sender_is_human(message):
            continue
        if (
            recognize_alpha_invocation(
                text,
                bot_username=bot_username,
                reply_to_bot=_is_reply_to_bot(
                    message,
                    bot_username=bot_username,
                ),
            )
            is AlphaInvocationKind.NONE
        ):
            continue
        context_message = AgentContextMessage(
            message_id=str(canonical.id),
            role="user",
            content=text,
        )
        events.append(
            (
                receipt.received_at,
                0,
                context_message.message_id,
                context_message,
            )
        )

    # Select the bounded set before joins that may multiply one attempt by corrupt
    # duplicate sessions/turns. Attempt->grant is one-to-one here (grant_id is unique),
    # so each row in this subquery is one distinct send attempt by construction.
    recent_attempt_ids = (
        select(AgentSendAttemptModel.id.label("attempt_id"))
        .join(
            DeliveryGrantModel,
            (DeliveryGrantModel.id == AgentSendAttemptModel.grant_id)
            & (
                DeliveryGrantModel.workspace_id
                == AgentSendAttemptModel.workspace_id
            ),
        )
        .where(
            AgentSendAttemptModel.workspace_id == workspace_id,
            AgentSendAttemptModel.state == "sent",
            AgentSendAttemptModel.provider_message_id.is_not(None),
            AgentSendAttemptModel.updated_at < trigger_received_at,
            DeliveryGrantModel.workspace_id == workspace_id,
            DeliveryGrantModel.installation_id == installation_id,
            DeliveryGrantModel.used_at.is_not(None),
            DeliveryGrantModel.revoked_at.is_(None),
        )
        .order_by(
            AgentSendAttemptModel.updated_at.desc(),
            AgentSendAttemptModel.id.desc(),
        )
        .limit(64)
        .subquery("recent_alpha_sent_attempts")
    )
    delivered_trigger = aliased(CanonicalMessage)
    delivered_receipt = aliased(TelegramReceipt)
    delivered_rows = (
        await session.execute(
            select(
                AgentSendAttemptModel,
                DeliveryGrantModel,
                AgentInstallationModel,
                AgentSessionModel,
                AgentTurnModel,
                AgentDeploymentModel,
                InstructionRevisionModel,
                AgentDefinitionModel,
                delivered_trigger,
                delivered_receipt,
            )
            .join(
                recent_attempt_ids,
                recent_attempt_ids.c.attempt_id == AgentSendAttemptModel.id,
            )
            .join(
                DeliveryGrantModel,
                (DeliveryGrantModel.id == AgentSendAttemptModel.grant_id)
                & (
                    DeliveryGrantModel.workspace_id
                    == AgentSendAttemptModel.workspace_id
                ),
            )
            .join(
                AgentInstallationModel,
                (AgentInstallationModel.id == DeliveryGrantModel.installation_id)
                & (
                    AgentInstallationModel.workspace_id
                    == DeliveryGrantModel.workspace_id
                ),
            )
            .join(
                AgentSessionModel,
                (AgentSessionModel.workspace_id == AgentSendAttemptModel.workspace_id)
                & (AgentSessionModel.dialog_id == AgentInstallationModel.dialog_id),
            )
            .join(
                AgentTurnModel,
                (AgentTurnModel.workspace_id == AgentSendAttemptModel.workspace_id)
                & (AgentTurnModel.session_id == AgentSessionModel.id)
                & (
                    AgentTurnModel.trigger_message_id
                    == DeliveryGrantModel.trigger_message_id
                )
                & (
                    AgentTurnModel.instruction_revision_id
                    == AgentSessionModel.instruction_revision_id
                )
                & (
                    AgentTurnModel.candidate_sha256
                    == DeliveryGrantModel.candidate_sha256
                ),
            )
            .join(
                AgentDeploymentModel,
                (AgentDeploymentModel.id == AgentSessionModel.deployment_id)
                & (
                    AgentDeploymentModel.workspace_id
                    == AgentSessionModel.workspace_id
                )
                & (
                    AgentDeploymentModel.instruction_revision_id
                    == AgentSessionModel.instruction_revision_id
                ),
            )
            .join(
                InstructionRevisionModel,
                (InstructionRevisionModel.id == AgentSessionModel.instruction_revision_id)
                & (
                    InstructionRevisionModel.workspace_id
                    == AgentSessionModel.workspace_id
                )
                & (
                    InstructionRevisionModel.id
                    == AgentDeploymentModel.instruction_revision_id
                ),
            )
            .join(
                AgentDefinitionModel,
                (AgentDefinitionModel.id == AgentDeploymentModel.agent_definition_id)
                & (
                    AgentDefinitionModel.id
                    == InstructionRevisionModel.agent_definition_id
                )
                & (
                    AgentDefinitionModel.workspace_id
                    == AgentSessionModel.workspace_id
                ),
            )
            .join(
                delivered_trigger,
                (delivered_trigger.id == DeliveryGrantModel.trigger_message_id)
                & (delivered_trigger.workspace_id == DeliveryGrantModel.workspace_id)
                & (delivered_trigger.dialog_id == AgentInstallationModel.dialog_id),
            )
            .join(
                delivered_receipt,
                delivered_receipt.id == delivered_trigger.receipt_id,
            )
            .where(
                AgentSendAttemptModel.workspace_id == workspace_id,
                AgentSendAttemptModel.state == "sent",
                AgentSendAttemptModel.provider_message_id.is_not(None),
                AgentSendAttemptModel.updated_at < trigger_received_at,
                DeliveryGrantModel.used_at.is_not(None),
                DeliveryGrantModel.revoked_at.is_(None),
                AgentInstallationModel.id == installation_id,
                AgentInstallationModel.workspace_id == workspace_id,
                AgentInstallationModel.dialog_id == dialog.id,
                AgentSessionModel.workspace_id == workspace_id,
                AgentSessionModel.dialog_id == dialog.id,
                AgentDeploymentModel.workspace_id == workspace_id,
                InstructionRevisionModel.workspace_id == workspace_id,
                InstructionRevisionModel.validation_state == "validated",
                AgentDefinitionModel.workspace_id == workspace_id,
                AgentDefinitionModel.agent_key == AgentKey.ALPHA_GROUP.value,
                AgentTurnModel.status == "proposed",
                AgentTurnModel.result_kind == "reply",
                delivered_trigger.direction == "inbound",
                delivered_receipt.bot_key == connector_key,
                delivered_receipt.received_at < trigger_received_at,
            )
            .order_by(
                AgentSendAttemptModel.updated_at.desc(),
                AgentSendAttemptModel.id.desc(),
            )
        )
    ).all()
    delivered_by_attempt: dict[str, tuple[datetime, AgentContextMessage] | None] = {}
    for (
        attempt,
        context_grant,
        context_installation,
        context_session,
        context_turn,
        context_deployment,
        context_revision,
        context_definition,
        source_trigger,
        source_receipt,
    ) in delivered_rows:
        attempt_key = str(attempt.id)
        if delivered_by_attempt.get(attempt_key) is None and attempt_key in delivered_by_attempt:
            continue
        if (
            attempt.workspace_id != workspace_id
            or attempt.state != "sent"
            or not isinstance(attempt.provider_message_id, str)
            or not attempt.provider_message_id.strip()
            or context_grant.id != attempt.grant_id
            or context_grant.workspace_id != workspace_id
            or context_grant.installation_id != installation_id
            or context_grant.used_at is None
            or context_grant.revoked_at is not None
            or context_installation.id != installation_id
            or context_installation.workspace_id != workspace_id
            or context_installation.dialog_id != dialog.id
            or context_session.workspace_id != workspace_id
            or context_session.dialog_id != dialog.id
            or context_deployment.id != context_session.deployment_id
            or context_deployment.workspace_id != workspace_id
            or context_deployment.instruction_revision_id
            != context_session.instruction_revision_id
            or context_revision.id != context_session.instruction_revision_id
            or context_revision.workspace_id != workspace_id
            or context_revision.id != context_deployment.instruction_revision_id
            or context_revision.validation_state != "validated"
            or context_definition.id != context_deployment.agent_definition_id
            or context_definition.id != context_revision.agent_definition_id
            or context_definition.workspace_id != workspace_id
            or context_definition.agent_key != AgentKey.ALPHA_GROUP.value
            or context_turn.workspace_id != workspace_id
            or context_turn.session_id != context_session.id
            or context_turn.trigger_message_id != context_grant.trigger_message_id
            or context_turn.instruction_revision_id
            != context_session.instruction_revision_id
            or context_turn.status != "proposed"
            or context_turn.result_kind != "reply"
            or source_trigger.id != context_grant.trigger_message_id
            or source_trigger.workspace_id != workspace_id
            or source_trigger.dialog_id != dialog.id
            or source_trigger.direction != "inbound"
            or source_receipt.id != source_trigger.receipt_id
            or source_receipt.bot_key != connector_key
            or attempt.updated_at >= trigger_received_at
            or source_receipt.received_at >= trigger_received_at
        ):
            continue
        try:
            source_message = _verified_trigger_message(
                source_receipt.payload,
                canonical=source_trigger,
                dialog=dialog,
            )
        except AlphaShadowWorkerError:
            continue
        if not _sender_is_human(source_message):
            continue
        source_text = source_message.get("text")
        if not isinstance(source_text, str) or not source_text.strip():
            continue
        if (
            recognize_alpha_invocation(
                source_text,
                bot_username=bot_username,
                reply_to_bot=_is_reply_to_bot(
                    source_message,
                    bot_username=bot_username,
                ),
            )
            is AlphaInvocationKind.NONE
        ):
            continue
        candidate_text = context_turn.candidate_text
        turn_candidate_sha256 = context_turn.candidate_sha256
        grant_candidate_sha256 = context_grant.candidate_sha256
        if (
            not isinstance(candidate_text, str)
            or not candidate_text.strip()
            or len(candidate_text) > MAX_TELEGRAM_TEXT_CHARS
            or not isinstance(turn_candidate_sha256, str)
            or turn_candidate_sha256 != grant_candidate_sha256
            or hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
            != grant_candidate_sha256
        ):
            continue
        context_message = AgentContextMessage(
            message_id=f"alpha-send-{attempt_key}",
            role="assistant",
            content=candidate_text,
        )
        existing = delivered_by_attempt.get(attempt_key)
        candidate = (attempt.updated_at, context_message)
        if existing is not None and existing != candidate:
            delivered_by_attempt[attempt_key] = None
            continue
        delivered_by_attempt[attempt_key] = candidate

    for delivered in delivered_by_attempt.values():
        if delivered is None:
            continue
        delivered_at, context_message = delivered
        events.append(
            (
                delivered_at,
                1,
                context_message.message_id,
                context_message,
            )
        )

    events.sort(key=lambda item: (item[0], item[1], item[2]))
    return tuple(item[3] for item in events[-32:])


def _require_trigger_connector(
    *,
    receipt: TelegramReceipt,
    connector_key: str,
) -> None:
    if receipt.bot_key != connector_key:
        raise AlphaShadowWorkerError("alpha_trigger_connector_mismatch")


def _verified_trigger_message(
    payload: Mapping[str, Any],
    *,
    canonical: CanonicalMessage,
    dialog: Dialog,
) -> Mapping[str, Any]:
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise AlphaShadowWorkerError("alpha_receipt_message_invalid")
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
        raise AlphaShadowWorkerError("alpha_receipt_identity_invalid")
    if (
        _provider_id(message.get("message_id")) != canonical.provider_message_id
        or _provider_id(chat.get("id")) != dialog.provider_dialog_id
        or _provider_id(sender.get("id")) != canonical.provider_sender_id
    ):
        raise AlphaShadowWorkerError("alpha_receipt_identity_mismatch")
    return cast(Mapping[str, Any], message)


def _sender_is_human(message: Mapping[str, Any]) -> bool:
    sender = message.get("from")
    return (
        isinstance(sender, Mapping)
        and sender.get("is_bot") is False
        and not isinstance(message.get("sender_chat"), Mapping)
    )


def _is_bot_message(
    message: Mapping[str, Any],
    *,
    bot_username: str,
) -> bool:
    sender = message.get("from")
    return (
        isinstance(sender, Mapping)
        and sender.get("is_bot") is True
        and isinstance(sender.get("username"), str)
        and cast(str, sender["username"]).casefold() == bot_username.casefold()
    )


def _is_reply_to_bot(
    message: Mapping[str, Any],
    *,
    bot_username: str,
) -> bool:
    reply = message.get("reply_to_message")
    return isinstance(reply, Mapping) and _is_bot_message(
        reply,
        bot_username=bot_username,
    )


def _snapshot(
    turn: AgentTurnModel,
    *,
    session_key: str,
) -> AlphaTurnSnapshot:
    return AlphaTurnSnapshot(
        turn_key=f"turn-{turn.id.hex}",
        session_key=session_key,
        status=AlphaTurnStatus(turn.status),
        result_kind=(
            AgentResultKind(turn.result_kind)
            if turn.result_kind is not None
            else None
        ),
        result_code=turn.result_code,
        candidate_sha256=turn.candidate_sha256,
    )


def _stable_key(prefix: str, *parts: str) -> str:
    return f"{prefix}-{uuid.uuid5(uuid.NAMESPACE_URL, ':'.join(parts)).hex}"


def _turn_id(turn_key: str) -> uuid.UUID:
    if not turn_key.startswith("turn-"):
        raise AlphaShadowWorkerError("alpha_turn_key_invalid")
    return _uuid(turn_key.removeprefix("turn-"), "turn_key")


def _uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise AlphaShadowWorkerError(f"alpha_{field_name}_invalid") from exc


def _provider_id(value: object) -> str | None:
    if not isinstance(value, int) or isinstance(value, bool) or value == 0:
        return None
    return str(value)
