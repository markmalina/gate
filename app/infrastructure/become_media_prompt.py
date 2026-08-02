"""PostgreSQL and Telegram adapters for the controlled BECOME media prompt."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import httpx
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_intake import (
    BECOME_CONNECTOR_KEY,
    BecomeControlledIntakeError,
)
from app.application.become_media_prompt import (
    BECOME_MEDIA_PROMPT_SCHEMA,
    BECOME_MEDIA_PROMPT_TEXT,
    BecomeMediaPromptCandidate,
    BecomeMediaPromptProviderError,
    BecomeMediaPromptReservation,
    BecomeMediaPromptState,
    become_media_prompt_digest,
)
from app.models.alpha import AgentInstallationModel
from app.models.become import (
    BecomeControlledConsentReceiptModel,
    BecomeControlledIntakeReceiptModel,
    BecomeMediaIntakeModel,
    BecomeMediaPromptModel,
    BecomeRelationsConsentHandoffModel,
)
from app.models.canonical import Dialog, TransportConnector, Workspace
from app.services.telegram import TelegramApiError, TelegramClient


class SessionFactoryBecomeMediaPromptStore:
    """Reserve exactly one prompt after all consent and isolation gates."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def candidate(
        self,
        *,
        workspace_key: str,
    ) -> BecomeMediaPromptCandidate | None:
        async with self._sessions() as session:
            rows = await _eligible_rows(
                session,
                workspace_key=workspace_key,
                lock=False,
            )
        if len(rows) != 1:
            return None
        installation, dialog, consent, handoff = rows[0]
        return BecomeMediaPromptCandidate(
            candidate_key=_candidate_key(
                installation_id=installation.id,
                consent_id=consent.id,
                execution_digest=handoff.execution_digest,
            ),
            installation_ref=installation.installation_key,
            target_chat_id=dialog.provider_dialog_id,
        )

    async def reserve(
        self,
        *,
        workspace_key: str,
        candidate_key: str,
        now: datetime,
    ) -> BecomeMediaPromptReservation:
        async with self._sessions.begin() as session:
            rows = await _eligible_rows(
                session,
                workspace_key=workspace_key,
                lock=True,
            )
            if len(rows) != 1:
                raise BecomeControlledIntakeError(
                    "become_media_prompt_gate_not_ready"
                )
            installation, dialog, consent, handoff = rows[0]
            expected_key = _candidate_key(
                installation_id=installation.id,
                consent_id=consent.id,
                execution_digest=handoff.execution_digest,
            )
            if candidate_key != expected_key:
                raise BecomeControlledIntakeError(
                    "become_media_prompt_candidate_changed"
                )

            existing = await session.scalar(
                select(BecomeMediaPromptModel)
                .where(
                    BecomeMediaPromptModel.installation_id
                    == installation.id
                )
                .with_for_update()
            )
            if existing is not None:
                if (
                    existing.prompt_key != expected_key
                    or existing.consent_receipt_id != consent.id
                    or existing.relations_handoff_id != handoff.id
                    or existing.prompt_digest
                    != become_media_prompt_digest()
                ):
                    raise BecomeControlledIntakeError(
                        "become_media_prompt_mutated_replay"
                    )
                if existing.state == BecomeMediaPromptState.SENDING.value:
                    existing.state = BecomeMediaPromptState.UNKNOWN.value
                    existing.error_code = (
                        "become_media_prompt_prior_outcome_unknown"
                    )
                    existing.updated_at = now
                    await session.flush()
                return _reservation(
                    existing,
                    idempotent_replay=True,
                )

            prompt = BecomeMediaPromptModel(
                workspace_id=installation.workspace_id,
                installation_id=installation.id,
                consent_receipt_id=consent.id,
                relations_handoff_id=handoff.id,
                prompt_key=expected_key,
                schema_name=BECOME_MEDIA_PROMPT_SCHEMA,
                state=BecomeMediaPromptState.SENDING.value,
                prompt_digest=become_media_prompt_digest(),
                created_at=now,
                updated_at=now,
            )
            session.add(prompt)
            await session.flush()
            return BecomeMediaPromptReservation(
                attempt_key=f"become-prompt-{prompt.id.hex}",
                state=BecomeMediaPromptState.SENDING,
                target_chat_id=dialog.provider_dialog_id,
                prompt_text=BECOME_MEDIA_PROMPT_TEXT,
            )

    async def finalize(
        self,
        *,
        workspace_key: str,
        attempt_key: str,
        state: BecomeMediaPromptState,
        finished_at: datetime,
        provider_message_id: str | None = None,
        error_code: str | None = None,
    ) -> BecomeMediaPromptReservation:
        if state not in {
            BecomeMediaPromptState.SENT,
            BecomeMediaPromptState.FAILED,
            BecomeMediaPromptState.BLOCKED,
            BecomeMediaPromptState.UNKNOWN,
        }:
            raise BecomeControlledIntakeError(
                "become_media_prompt_terminal_state_invalid"
            )
        _validate_outcome(
            state=state,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )
        prompt_id = _attempt_id(attempt_key)
        async with self._sessions.begin() as session:
            prompt = await session.scalar(
                select(BecomeMediaPromptModel)
                .join(
                    Workspace,
                    Workspace.id == BecomeMediaPromptModel.workspace_id,
                )
                .where(
                    BecomeMediaPromptModel.id == prompt_id,
                    Workspace.key == workspace_key,
                    Workspace.is_active.is_(True),
                )
                .with_for_update()
            )
            if prompt is None:
                raise BecomeControlledIntakeError(
                    "become_media_prompt_not_found"
                )
            if prompt.state != BecomeMediaPromptState.SENDING.value:
                expected = (state.value, provider_message_id, error_code)
                current = (
                    prompt.state,
                    prompt.provider_message_id,
                    prompt.error_code,
                )
                if current != expected:
                    raise BecomeControlledIntakeError(
                        "become_media_prompt_outcome_conflict"
                    )
                return _reservation(prompt, idempotent_replay=True)

            prompt.state = state.value
            prompt.provider_message_id = provider_message_id
            prompt.error_code = error_code
            prompt.sent_at = (
                finished_at
                if state is BecomeMediaPromptState.SENT
                else None
            )
            prompt.updated_at = finished_at
            await session.flush()
            return _reservation(prompt, idempotent_replay=False)


class TelegramClientBecomeMediaPromptAdapter:
    """Expose only one literal BECOME sendMessage call."""

    def __init__(self, telegram: TelegramClient) -> None:
        self._telegram = telegram

    async def send_text(self, *, chat_id: str, text: str) -> str:
        if text != BECOME_MEDIA_PROMPT_TEXT:
            raise BecomeControlledIntakeError(
                "become_media_prompt_text_invalid"
            )
        try:
            response = await self._telegram.send_plain_message(
                chat_id=chat_id,
                text=text,
            )
        except TelegramApiError as exc:
            state = (
                BecomeMediaPromptState.BLOCKED
                if exc.error_code in {400, 403}
                else BecomeMediaPromptState.FAILED
            )
            suffix = (
                str(exc.error_code)
                if exc.error_code is not None
                else "error"
            )
            raise BecomeMediaPromptProviderError(
                state=state,
                error_code=f"telegram_api_{suffix}",
            ) from None
        except (httpx.TransportError, RuntimeError):
            raise BecomeMediaPromptProviderError(
                state=BecomeMediaPromptState.UNKNOWN,
                error_code="telegram_outcome_unknown",
            ) from None

        result = response.get("result")
        message_id = (
            result.get("message_id")
            if isinstance(result, dict)
            else None
        )
        if (
            not isinstance(message_id, (int, str))
            or isinstance(message_id, bool)
            or not str(message_id).strip()
        ):
            raise BecomeMediaPromptProviderError(
                state=BecomeMediaPromptState.UNKNOWN,
                error_code="telegram_message_id_invalid",
            )
        return str(message_id)


async def become_media_prompt_status(
    session: AsyncSession,
    *,
    workspace_key: str,
) -> dict[str, int]:
    row = (
        await session.execute(
            select(
                func.count(BecomeMediaPromptModel.id),
                func.count(BecomeMediaPromptModel.id).filter(
                    BecomeMediaPromptModel.state == "sent"
                ),
                func.count(BecomeMediaPromptModel.id).filter(
                    BecomeMediaPromptModel.state == "unknown"
                ),
                func.count(BecomeMediaPromptModel.id).filter(
                    BecomeMediaPromptModel.state.in_(("failed", "blocked"))
                ),
            )
            .join(
                Workspace,
                Workspace.id == BecomeMediaPromptModel.workspace_id,
            )
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                BecomeMediaPromptModel.schema_name
                == BECOME_MEDIA_PROMPT_SCHEMA,
            )
        )
    ).one()
    return {
        "media_prompt_count": int(row[0] or 0),
        "media_prompt_sent_count": int(row[1] or 0),
        "media_prompt_unknown_count": int(row[2] or 0),
        "media_prompt_failed_count": int(row[3] or 0),
    }


async def _eligible_rows(
    session: AsyncSession,
    *,
    workspace_key: str,
    lock: bool,
) -> list[
    tuple[
        AgentInstallationModel,
        Dialog,
        BecomeControlledConsentReceiptModel,
        BecomeRelationsConsentHandoffModel,
    ]
]:
    statement = (
        select(
            AgentInstallationModel,
            Dialog,
            BecomeControlledConsentReceiptModel,
            BecomeRelationsConsentHandoffModel,
        )
        .select_from(Workspace)
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
            AgentInstallationModel,
            (
                AgentInstallationModel.workspace_id
                == Workspace.id
            )
            & (
                AgentInstallationModel.connector_id
                == TransportConnector.id
            )
            & (AgentInstallationModel.dialog_id == Dialog.id),
        )
        .join(
            BecomeControlledIntakeReceiptModel,
            (
                BecomeControlledIntakeReceiptModel.workspace_id
                == Workspace.id
            )
            & (
                BecomeControlledIntakeReceiptModel.installation_id
                == AgentInstallationModel.id
            ),
        )
        .join(
            BecomeControlledConsentReceiptModel,
            (
                BecomeControlledConsentReceiptModel.workspace_id
                == Workspace.id
            )
            & (
                BecomeControlledConsentReceiptModel.installation_id
                == AgentInstallationModel.id
            )
            & (
                BecomeControlledConsentReceiptModel.subject_ref
                == BecomeControlledIntakeReceiptModel.subject_ref
            ),
        )
        .join(
            BecomeRelationsConsentHandoffModel,
            BecomeRelationsConsentHandoffModel.consent_receipt_id
            == BecomeControlledConsentReceiptModel.id,
        )
        .where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
            TransportConnector.connector_key
            == BECOME_CONNECTOR_KEY,
            TransportConnector.connector_kind == "telegram_bot",
            TransportConnector.is_enabled.is_(True),
            AgentInstallationModel.state == "active",
            BecomeControlledIntakeReceiptModel.intent
            == "registration_confirmed",
            BecomeControlledIntakeReceiptModel.group_state
            == "owner_only",
            BecomeControlledIntakeReceiptModel.member_count == 2,
            BecomeControlledConsentReceiptModel.intent
            == "consent_accepted",
            BecomeControlledConsentReceiptModel.group_state
            == "owner_only",
            BecomeControlledConsentReceiptModel.member_count == 2,
            BecomeRelationsConsentHandoffModel.status == "executed",
            BecomeRelationsConsentHandoffModel.execution_digest.is_not(
                None
            ),
            ~exists(
                select(BecomeMediaIntakeModel.id).where(
                    BecomeMediaIntakeModel.installation_id
                    == AgentInstallationModel.id
                )
            ),
        )
        .limit(2)
    )
    if lock:
        statement = statement.with_for_update(
            of=BecomeRelationsConsentHandoffModel
        )
    return list((await session.execute(statement)).tuples().all())


def _candidate_key(
    *,
    installation_id: uuid.UUID,
    consent_id: uuid.UUID,
    execution_digest: str | None,
) -> str:
    if (
        execution_digest is None
        or len(execution_digest) != 64
    ):
        raise BecomeControlledIntakeError(
            "become_media_prompt_execution_invalid"
        )
    digest = hashlib.sha256(
        (
            str(installation_id)
            + ":"
            + str(consent_id)
            + ":"
            + execution_digest
        ).encode("utf-8")
    ).hexdigest()
    return f"become_prompt_{digest[:24]}"


def _attempt_id(attempt_key: str) -> uuid.UUID:
    if not attempt_key.startswith("become-prompt-"):
        raise BecomeControlledIntakeError(
            "become_media_prompt_attempt_key_invalid"
        )
    try:
        return uuid.UUID(
            attempt_key.removeprefix("become-prompt-")
        )
    except ValueError as exc:
        raise BecomeControlledIntakeError(
            "become_media_prompt_attempt_key_invalid"
        ) from exc


def _validate_outcome(
    *,
    state: BecomeMediaPromptState,
    provider_message_id: str | None,
    error_code: str | None,
) -> None:
    if state is BecomeMediaPromptState.SENT:
        if (
            provider_message_id is None
            or not provider_message_id.strip()
            or len(provider_message_id) > 255
            or error_code is not None
        ):
            raise BecomeControlledIntakeError(
                "become_media_prompt_success_invalid"
            )
        return
    if provider_message_id is not None:
        raise BecomeControlledIntakeError(
            "become_media_prompt_failure_provider_id_invalid"
        )
    if (
        error_code is None
        or not error_code.strip()
        or len(error_code) > 128
    ):
        raise BecomeControlledIntakeError(
            "become_media_prompt_failure_code_invalid"
        )


def _reservation(
    prompt: BecomeMediaPromptModel,
    *,
    idempotent_replay: bool,
) -> BecomeMediaPromptReservation:
    return BecomeMediaPromptReservation(
        attempt_key=f"become-prompt-{prompt.id.hex}",
        state=BecomeMediaPromptState(prompt.state),
        provider_message_id=prompt.provider_message_id,
        error_code=prompt.error_code,
        idempotent_replay=idempotent_replay,
    )
