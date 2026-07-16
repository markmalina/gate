"""PostgreSQL grant consumption and literal Telegram adapter for Alpha."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.alpha_delivery import (
    MAX_TELEGRAM_TEXT_CHARS,
    AlphaDeliveryError,
    AlphaDeliveryReservation,
    AlphaDeliveryStore,
    AlphaProviderDeliveryError,
    AlphaTelegramTextPort,
    DeliverAlphaGrantCommand,
)
from app.application.alpha_group import (
    AgentSendAttemptState,
    AlphaInstallationState,
)
from app.application.canonical_messaging import DialogKind
from app.models.agent import AgentSessionModel, AgentTurnModel
from app.models.alpha import (
    AgentInstallationModel,
    AgentSendAttemptModel,
    DeliveryGrantModel,
)
from app.models.canonical import Dialog, TransportConnector, Workspace
from app.services.telegram import TelegramApiError, TelegramClient


class SessionFactoryAlphaDeliveryStore(AlphaDeliveryStore):
    """Consume and bind one grant in a short transaction before provider I/O."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def reserve(
        self, command: DeliverAlphaGrantCommand
    ) -> AlphaDeliveryReservation:
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        DeliveryGrantModel,
                        AgentInstallationModel,
                        Dialog,
                        TransportConnector,
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
                        Dialog,
                        (Dialog.id == AgentInstallationModel.dialog_id)
                        & (Dialog.workspace_id == DeliveryGrantModel.workspace_id),
                    )
                    .join(
                        TransportConnector,
                        (TransportConnector.id == AgentInstallationModel.connector_id)
                        & (
                            TransportConnector.workspace_id
                            == DeliveryGrantModel.workspace_id
                        ),
                    )
                    .join(
                        Workspace,
                        Workspace.id == DeliveryGrantModel.workspace_id,
                    )
                    .where(
                        Workspace.key == command.workspace_key,
                        Workspace.is_active.is_(True),
                        DeliveryGrantModel.grant_key == command.grant_key,
                    )
                    .with_for_update(of=DeliveryGrantModel)
                )
            ).one_or_none()
            if row is None:
                raise AlphaDeliveryError("alpha_delivery_grant_not_found")
            grant, installation, dialog, connector = row

            existing = await session.scalar(
                select(AgentSendAttemptModel)
                .where(AgentSendAttemptModel.grant_id == grant.id)
                .with_for_update()
            )
            if existing is not None:
                if existing.state in {
                    AgentSendAttemptState.RESERVED.value,
                    AgentSendAttemptState.SENDING.value,
                }:
                    existing.state = AgentSendAttemptState.UNKNOWN.value
                    existing.error_code = "alpha_prior_attempt_outcome_unknown"
                    existing.updated_at = command.now
                    await session.flush()
                return _terminal_reservation(existing, idempotent_replay=True)

            block_code = _grant_block_code(
                grant=grant,
                installation=installation,
                dialog=dialog,
                connector=connector,
                now=command.now,
            )
            candidate_text: str | None = None
            if block_code is None:
                candidate_text = await _candidate_text(
                    session,
                    grant=grant,
                    installation=installation,
                )
                if candidate_text is None:
                    block_code = "alpha_grant_candidate_unavailable"

            attempt = AgentSendAttemptModel(
                workspace_id=grant.workspace_id,
                grant_id=grant.id,
                state=(
                    AgentSendAttemptState.BLOCKED.value
                    if block_code is not None
                    else AgentSendAttemptState.SENDING.value
                ),
                error_code=block_code,
                created_at=command.now,
                updated_at=command.now,
            )
            session.add(attempt)
            if block_code is not None:
                if grant.used_at is None and grant.revoked_at is None:
                    grant.revoked_at = command.now
                await session.flush()
                return _terminal_reservation(attempt, idempotent_replay=False)

            assert candidate_text is not None
            grant.used_at = command.now
            await session.flush()
            return AlphaDeliveryReservation(
                attempt_key=f"attempt-{attempt.id.hex}",
                state=AgentSendAttemptState.SENDING,
                target_chat_id=dialog.provider_dialog_id,
                candidate_text=candidate_text,
            )

    async def finalize(
        self,
        *,
        workspace_key: str,
        attempt_key: str,
        state: AgentSendAttemptState,
        finished_at: datetime,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> AlphaDeliveryReservation:
        if state not in {
            AgentSendAttemptState.SENT,
            AgentSendAttemptState.FAILED,
            AgentSendAttemptState.BLOCKED,
            AgentSendAttemptState.UNKNOWN,
        }:
            raise AlphaDeliveryError("alpha_delivery_terminal_state_invalid")
        _validate_outcome(
            state=state,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )
        attempt_id = _attempt_id(attempt_key)
        async with self._sessions.begin() as session:
            attempt = await session.scalar(
                select(AgentSendAttemptModel)
                .join(
                    Workspace,
                    Workspace.id == AgentSendAttemptModel.workspace_id,
                )
                .where(
                    AgentSendAttemptModel.id == attempt_id,
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                )
                .with_for_update()
            )
            if attempt is None:
                raise AlphaDeliveryError("alpha_send_attempt_not_found")
            if attempt.state != AgentSendAttemptState.SENDING.value:
                expected = (state.value, provider_message_id, error_code)
                current = (
                    attempt.state,
                    attempt.provider_message_id,
                    attempt.error_code,
                )
                if current != expected:
                    raise AlphaDeliveryError("alpha_send_attempt_outcome_conflict")
                return _terminal_reservation(attempt, idempotent_replay=True)

            attempt.state = state.value
            attempt.provider_message_id = provider_message_id
            attempt.error_code = error_code
            attempt.updated_at = finished_at
            await session.flush()
            return _terminal_reservation(attempt, idempotent_replay=False)


class TelegramClientAlphaTextAdapter(AlphaTelegramTextPort):
    """Expose only literal sendMessage(chat_id, text) to Alpha delivery."""

    def __init__(self, telegram: TelegramClient) -> None:
        self._telegram = telegram

    async def send_text(self, *, chat_id: str, text: str) -> str:
        try:
            response = await self._telegram.send_plain_message(
                chat_id=chat_id,
                text=text,
            )
        except TelegramApiError as exc:
            state = (
                AgentSendAttemptState.BLOCKED
                if exc.error_code in {400, 403}
                else AgentSendAttemptState.FAILED
            )
            suffix = str(exc.error_code) if exc.error_code is not None else "error"
            raise AlphaProviderDeliveryError(
                state=state,
                error_code=f"telegram_api_{suffix}",
            ) from None
        except (httpx.TransportError, RuntimeError):
            raise AlphaProviderDeliveryError(
                state=AgentSendAttemptState.UNKNOWN,
                error_code="telegram_outcome_unknown",
            ) from None

        result = response.get("result")
        if not isinstance(result, dict):
            raise AlphaProviderDeliveryError(
                state=AgentSendAttemptState.UNKNOWN,
                error_code="telegram_result_invalid",
            )
        message_id = result.get("message_id")
        if (
            not isinstance(message_id, (int, str))
            or isinstance(message_id, bool)
            or not str(message_id).strip()
        ):
            raise AlphaProviderDeliveryError(
                state=AgentSendAttemptState.UNKNOWN,
                error_code="telegram_message_id_invalid",
            )
        return str(message_id)


async def _candidate_text(
    session: AsyncSession,
    *,
    grant: DeliveryGrantModel,
    installation: AgentInstallationModel,
) -> str | None:
    if (
        installation.deployment_id is None
        or installation.instruction_revision_id is None
    ):
        return None
    candidates = (
        await session.execute(
            select(AgentTurnModel.candidate_text)
            .join(
                AgentSessionModel,
                AgentSessionModel.id == AgentTurnModel.session_id,
            )
            .where(
                AgentTurnModel.workspace_id == grant.workspace_id,
                AgentTurnModel.trigger_message_id == grant.trigger_message_id,
                AgentTurnModel.instruction_revision_id
                == installation.instruction_revision_id,
                AgentTurnModel.status == "proposed",
                AgentTurnModel.result_kind == "reply",
                AgentTurnModel.candidate_sha256 == grant.candidate_sha256,
                AgentSessionModel.workspace_id == grant.workspace_id,
                AgentSessionModel.dialog_id == installation.dialog_id,
                AgentSessionModel.deployment_id == installation.deployment_id,
                AgentSessionModel.instruction_revision_id
                == installation.instruction_revision_id,
            )
            .limit(2)
        )
    ).scalars().all()
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    if (
        candidate is None
        or not candidate.strip()
        or len(candidate) > MAX_TELEGRAM_TEXT_CHARS
        or hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        != grant.candidate_sha256
    ):
        return None
    return candidate


def _grant_block_code(
    *,
    grant: DeliveryGrantModel,
    installation: AgentInstallationModel,
    dialog: Dialog,
    connector: TransportConnector,
    now: datetime,
) -> str | None:
    if grant.revoked_at is not None:
        return "alpha_delivery_grant_revoked"
    if grant.used_at is not None:
        return "alpha_delivery_grant_already_used"
    if grant.expires_at.tzinfo is None or now >= grant.expires_at:
        return "alpha_delivery_grant_expired"
    if installation.state != AlphaInstallationState.ACTIVE.value:
        return "alpha_delivery_installation_inactive"
    if connector.connector_kind != "telegram_bot" or not connector.is_enabled:
        return "alpha_delivery_connector_disabled"
    if dialog.dialog_kind not in {
        DialogKind.GROUP.value,
        DialogKind.SUPERGROUP.value,
    }:
        return "alpha_delivery_dialog_not_group"
    return None


def _validate_outcome(
    *,
    state: AgentSendAttemptState,
    provider_message_id: str | None,
    error_code: str | None,
) -> None:
    if state is AgentSendAttemptState.SENT:
        if (
            provider_message_id is None
            or not provider_message_id.strip()
            or len(provider_message_id) > 255
            or error_code is not None
        ):
            raise AlphaDeliveryError("alpha_delivery_success_invalid")
        return
    if provider_message_id is not None:
        raise AlphaDeliveryError("alpha_delivery_failure_provider_id_invalid")
    if error_code is None or not error_code.strip() or len(error_code) > 128:
        raise AlphaDeliveryError("alpha_delivery_failure_code_invalid")


def _terminal_reservation(
    attempt: AgentSendAttemptModel,
    *,
    idempotent_replay: bool,
) -> AlphaDeliveryReservation:
    return AlphaDeliveryReservation(
        attempt_key=f"attempt-{attempt.id.hex}",
        state=AgentSendAttemptState(attempt.state),
        provider_message_id=attempt.provider_message_id,
        error_code=attempt.error_code,
        idempotent_replay=idempotent_replay,
    )


def _attempt_id(attempt_key: str) -> uuid.UUID:
    if not attempt_key.startswith("attempt-"):
        raise AlphaDeliveryError("alpha_send_attempt_key_invalid")
    try:
        return uuid.UUID(attempt_key.removeprefix("attempt-"))
    except ValueError as exc:
        raise AlphaDeliveryError("alpha_send_attempt_key_invalid") from exc
