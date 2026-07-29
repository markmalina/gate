"""Commit-before-send Telegram Business delivery for Universal Mayak.

The adapter resolves the exact target only from Gate's durable grant, installed
private dialog and canonical/legacy Business message binding. The model never
selects an account, connection, dialog or recipient.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.universal_mayak_demo import (
    DemoDeliveryReceipt,
    DemoDeliveryState,
    UniversalMayakDeliveryPort,
)
from app.models.agent import AgentSessionModel, AgentTurnModel
from app.models.alpha import (
    AgentInstallationModel,
    AgentSendAttemptModel,
    DeliveryGrantModel,
)
from app.models.canonical import (
    CanonicalMessage,
    Dialog,
    TransportConnector,
    Workspace,
)
from app.models.domain import Account, Message, TelegramBusinessConnection
from app.services.telegram import TelegramApiError, TelegramClient

_MAX_TELEGRAM_TEXT_CHARS = 4_096
_TERMINAL_STATES = frozenset({"sent", "failed", "unknown", "blocked"})


@dataclass(frozen=True, slots=True)
class _BusinessSendReservation:
    grant_id: uuid.UUID
    attempt_id: uuid.UUID
    turn_id: uuid.UUID
    chat_id: str
    business_connection_id: str
    text: str


def _business_binding_query() -> Select[
    tuple[
        DeliveryGrantModel,
        AgentSendAttemptModel,
        AgentInstallationModel,
        Dialog,
        TransportConnector,
        CanonicalMessage,
        Message,
        Account,
        TelegramBusinessConnection,
        AgentTurnModel,
    ]
]:
    return (
        select(
            DeliveryGrantModel,
            AgentSendAttemptModel,
            AgentInstallationModel,
            Dialog,
            TransportConnector,
            CanonicalMessage,
            Message,
            Account,
            TelegramBusinessConnection,
            AgentTurnModel,
        )
        .select_from(DeliveryGrantModel)
        .join(
            AgentSendAttemptModel,
            (AgentSendAttemptModel.grant_id == DeliveryGrantModel.id)
            & (
                AgentSendAttemptModel.workspace_id
                == DeliveryGrantModel.workspace_id
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
        .join(Workspace, Workspace.id == DeliveryGrantModel.workspace_id)
        .join(
            CanonicalMessage,
            (CanonicalMessage.id == DeliveryGrantModel.trigger_message_id)
            & (
                CanonicalMessage.workspace_id
                == DeliveryGrantModel.workspace_id
            )
            & (CanonicalMessage.dialog_id == Dialog.id),
        )
        .join(
            Message,
            Message.canonical_message_id == CanonicalMessage.id,
        )
        .join(
            Account,
            Account.id == Message.account_id,
        )
        .join(
            TelegramBusinessConnection,
            (TelegramBusinessConnection.account_id == Account.id)
            & (
                TelegramBusinessConnection.business_connection_id
                == Message.business_connection_id
            ),
        )
        .join(
            AgentTurnModel,
            (AgentTurnModel.workspace_id == DeliveryGrantModel.workspace_id)
            & (
                AgentTurnModel.trigger_message_id
                == DeliveryGrantModel.trigger_message_id
            )
            & (
                AgentTurnModel.candidate_sha256
                == DeliveryGrantModel.candidate_sha256
            ),
        )
        .join(
            AgentSessionModel,
            (AgentSessionModel.id == AgentTurnModel.session_id)
            & (
                AgentSessionModel.workspace_id
                == DeliveryGrantModel.workspace_id
            )
            & (AgentSessionModel.dialog_id == Dialog.id)
            & (
                AgentSessionModel.deployment_id
                == AgentInstallationModel.deployment_id
            )
            & (
                AgentSessionModel.instruction_revision_id
                == AgentInstallationModel.instruction_revision_id
            ),
        )
    )


class TelegramBusinessUniversalMayakDelivery(UniversalMayakDeliveryPort):
    """Use one exact durable Business target and never retry an unknown send."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        telegram: TelegramClient,
        enabled: bool,
    ) -> None:
        self._sessions = sessions
        self._telegram = telegram
        self._enabled = enabled

    async def deliver(self, delivery_ref: str) -> DemoDeliveryReceipt:
        grant_id = _grant_id(delivery_ref)
        if not self._enabled:
            return DemoDeliveryReceipt(
                delivery_ref,
                DemoDeliveryState.BLOCKED,
                error_code="mayak_business_delivery_disabled",
            )

        reserved = await self._reserve(grant_id)
        if isinstance(reserved, DemoDeliveryReceipt):
            return reserved

        return await self._send_and_finalize(reserved)

    async def recover_one_binding_block(self) -> DemoDeliveryReceipt | None:
        """Recover one exact pre-send binding block without invoking the model."""

        if not self._enabled:
            return None
        reservation = await self._reserve_recoverable_binding_block()
        if reservation is None:
            return None
        return await self._send_and_finalize(reservation)

    async def _send_and_finalize(
        self,
        reservation: _BusinessSendReservation,
    ) -> DemoDeliveryReceipt:
        try:
            response = await self._telegram.send_plain_message(
                chat_id=reservation.chat_id,
                text=reservation.text,
                business_connection_id=reservation.business_connection_id,
            )
            provider_message_id = _provider_message_id(response)
            if provider_message_id is None:
                state = DemoDeliveryState.UNKNOWN
                provider_message_id = None
                error_code = "telegram_message_id_unconfirmed"
            else:
                state = DemoDeliveryState.SENT
                error_code = None
        except Exception as exc:
            state, error_code = _provider_failure(exc)
            provider_message_id = None

        return await self._finalize(
            reservation,
            state=state,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )

    async def _reserve_recoverable_binding_block(
        self,
    ) -> _BusinessSendReservation | None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            rows = (
                await session.execute(
                    _business_binding_query()
                    .where(
                        AgentInstallationModel.installation_key
                        == "universal-mayak-v1-owner-private",
                        AgentInstallationModel.state == "paused",
                        DeliveryGrantModel.used_at.is_(None),
                        DeliveryGrantModel.revoked_at.is_not(None),
                        AgentSendAttemptModel.state == "blocked",
                        AgentSendAttemptModel.provider_message_id.is_(None),
                        AgentSendAttemptModel.error_code
                        == "mayak_business_binding_invalid",
                        AgentTurnModel.status == "failed",
                        AgentTurnModel.result_code
                        == "mayak_demo_delivery_blocked",
                    )
                    .limit(2)
                    .with_for_update(
                        of=(
                            DeliveryGrantModel,
                            AgentSendAttemptModel,
                            AgentTurnModel,
                        )
                    )
                )
            ).all()
            if not rows:
                return None
            if len(rows) != 1:
                raise RuntimeError("mayak_business_recovery_scope_ambiguous")
            (
                grant,
                attempt,
                installation,
                dialog,
                connector,
                canonical,
                legacy,
                account,
                connection,
                turn,
            ) = rows[0]
            block_code = _recovery_block_code(
                grant=grant,
                attempt=attempt,
                installation=installation,
                dialog=dialog,
                connector=connector,
                canonical=canonical,
                legacy=legacy,
                account=account,
                connection=connection,
                turn=turn,
            )
            if block_code is not None:
                raise RuntimeError(block_code)

            grant.revoked_at = None
            grant.used_at = now
            grant.expires_at = now + timedelta(minutes=5)
            attempt.state = "sending"
            attempt.error_code = None
            attempt.updated_at = now
            turn.status = "proposed"
            turn.result_code = "mayak_demo_delivery_recovery_sending"
            assert turn.candidate_text is not None
            return _BusinessSendReservation(
                grant_id=grant.id,
                attempt_id=attempt.id,
                turn_id=turn.id,
                chat_id=dialog.provider_dialog_id,
                business_connection_id=connection.business_connection_id,
                text=turn.candidate_text,
            )

    async def _reserve(
        self,
        grant_id: uuid.UUID,
    ) -> _BusinessSendReservation | DemoDeliveryReceipt:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            rows = (
                await session.execute(
                    _business_binding_query()
                    .where(DeliveryGrantModel.id == grant_id)
                    .with_for_update(
                        of=(
                            DeliveryGrantModel,
                            AgentSendAttemptModel,
                            AgentTurnModel,
                        )
                    )
                )
            ).all()
            if len(rows) != 1:
                return _blocked(str(grant_id), "mayak_business_binding_invalid")
            (
                grant,
                attempt,
                installation,
                dialog,
                connector,
                canonical,
                legacy,
                account,
                connection,
                turn,
            ) = rows[0]

            terminal = _terminal_receipt(str(grant.id), attempt)
            if terminal is not None:
                return terminal
            if attempt.state in {"reserved", "sending"} and attempt.state == "sending":
                attempt.state = "unknown"
                attempt.error_code = "mayak_business_prior_outcome_unknown"
                attempt.updated_at = now
                turn.status = "failed"
                turn.result_code = "mayak_demo_delivery_unknown"
                return _terminal_receipt(str(grant.id), attempt) or _blocked(
                    str(grant.id), "mayak_business_unknown_projection_failed"
                )

            block_code = _block_code(
                grant=grant,
                installation=installation,
                dialog=dialog,
                connector=connector,
                canonical=canonical,
                legacy=legacy,
                account=account,
                connection=connection,
                turn=turn,
                now=now,
            )
            if block_code is not None:
                attempt.state = "blocked"
                attempt.error_code = block_code
                attempt.updated_at = now
                if grant.used_at is None and grant.revoked_at is None:
                    grant.revoked_at = now
                turn.status = "failed"
                turn.result_code = "mayak_demo_delivery_blocked"
                return _blocked(str(grant.id), block_code)

            assert turn.candidate_text is not None
            attempt.state = "sending"
            attempt.error_code = None
            attempt.updated_at = now
            grant.used_at = now
            return _BusinessSendReservation(
                grant_id=grant.id,
                attempt_id=attempt.id,
                turn_id=turn.id,
                chat_id=dialog.provider_dialog_id,
                business_connection_id=connection.business_connection_id,
                text=turn.candidate_text,
            )

    async def _finalize(
        self,
        reservation: _BusinessSendReservation,
        *,
        state: DemoDeliveryState,
        provider_message_id: str | None,
        error_code: str | None,
    ) -> DemoDeliveryReceipt:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        DeliveryGrantModel,
                        AgentSendAttemptModel,
                        AgentTurnModel,
                    )
                    .join(
                        AgentSendAttemptModel,
                        AgentSendAttemptModel.grant_id == DeliveryGrantModel.id,
                    )
                    .join(
                        AgentTurnModel,
                        (AgentTurnModel.workspace_id == DeliveryGrantModel.workspace_id)
                        & (
                            AgentTurnModel.trigger_message_id
                            == DeliveryGrantModel.trigger_message_id
                        )
                        & (
                            AgentTurnModel.candidate_sha256
                            == DeliveryGrantModel.candidate_sha256
                        ),
                    )
                    .where(
                        DeliveryGrantModel.id == reservation.grant_id,
                        AgentSendAttemptModel.id == reservation.attempt_id,
                        AgentTurnModel.id == reservation.turn_id,
                    )
                    .with_for_update(
                        of=(AgentSendAttemptModel, AgentTurnModel)
                    )
                )
            ).one_or_none()
            if row is None:
                return DemoDeliveryReceipt(
                    str(reservation.grant_id),
                    DemoDeliveryState.UNKNOWN,
                    error_code="mayak_business_finalize_binding_missing",
                )
            grant, attempt, turn = row
            existing = _terminal_receipt(str(grant.id), attempt)
            if existing is not None:
                return existing
            if attempt.state != "sending":
                attempt.state = "unknown"
                attempt.provider_message_id = None
                attempt.error_code = "mayak_business_attempt_state_changed"
                attempt.updated_at = now
                turn.status = "failed"
                turn.result_code = "mayak_demo_delivery_unknown"
                return _terminal_receipt(str(grant.id), attempt) or _blocked(
                    str(grant.id), "mayak_business_unknown_projection_failed"
                )

            attempt.state = state.value
            attempt.provider_message_id = provider_message_id
            attempt.error_code = error_code
            attempt.updated_at = now
            turn.status = "proposed" if state is DemoDeliveryState.SENT else "failed"
            turn.result_code = {
                DemoDeliveryState.SENT: "mayak_demo_sent",
                DemoDeliveryState.FAILED: "mayak_demo_delivery_failed",
                DemoDeliveryState.UNKNOWN: "mayak_demo_delivery_unknown",
                DemoDeliveryState.BLOCKED: "mayak_demo_delivery_blocked",
            }[state]
            return DemoDeliveryReceipt(
                str(grant.id),
                state,
                provider_message_ref=provider_message_id,
                error_code=error_code,
            )


def _recovery_block_code(
    *,
    grant: DeliveryGrantModel,
    attempt: AgentSendAttemptModel,
    installation: AgentInstallationModel,
    dialog: Dialog,
    connector: TransportConnector,
    canonical: CanonicalMessage,
    legacy: Message,
    account: Account,
    connection: TelegramBusinessConnection,
    turn: AgentTurnModel,
) -> str | None:
    if (
        grant.used_at is not None
        or grant.revoked_at is None
        or attempt.state != "blocked"
        or attempt.provider_message_id is not None
        or attempt.error_code != "mayak_business_binding_invalid"
        or installation.installation_key != "universal-mayak-v1-owner-private"
        or installation.state != "paused"
    ):
        return "mayak_business_recovery_state_invalid"
    if connector.connector_kind != "telegram_business" or not connector.is_enabled:
        return "mayak_business_recovery_connector_invalid"
    if dialog.dialog_kind != "private":
        return "mayak_business_recovery_dialog_invalid"
    if (
        canonical.direction != "inbound"
        or canonical.provider_sender_id != dialog.provider_dialog_id
        or legacy.direction != "inbound"
        or legacy.chat_id != dialog.provider_dialog_id
        or legacy.sender_id != dialog.provider_dialog_id
        or legacy.business_connection_id != connection.business_connection_id
    ):
        return "mayak_business_recovery_trigger_invalid"
    if not account.is_active or not account.send_enabled:
        return "mayak_business_recovery_account_invalid"
    rights = connection.rights if isinstance(connection.rights, dict) else {}
    if not connection.is_enabled or rights.get("can_reply") is not True:
        return "mayak_business_recovery_connection_invalid"
    candidate = turn.candidate_text
    if (
        turn.status != "failed"
        or turn.result_kind != "reply"
        or turn.result_code != "mayak_demo_delivery_blocked"
        or candidate is None
        or not candidate.strip()
        or len(candidate) > _MAX_TELEGRAM_TEXT_CHARS
        or hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        != grant.candidate_sha256
    ):
        return "mayak_business_recovery_candidate_invalid"
    return None


def _block_code(
    *,
    grant: DeliveryGrantModel,
    installation: AgentInstallationModel,
    dialog: Dialog,
    connector: TransportConnector,
    canonical: CanonicalMessage,
    legacy: Message,
    account: Account,
    connection: TelegramBusinessConnection,
    turn: AgentTurnModel,
    now: datetime,
) -> str | None:
    if grant.used_at is not None or grant.revoked_at is not None:
        return "mayak_business_grant_consumed"
    if grant.expires_at.tzinfo is None or now >= grant.expires_at:
        return "mayak_business_grant_expired"
    if installation.state != "active":
        return "mayak_business_installation_inactive"
    if connector.connector_kind != "telegram_business" or not connector.is_enabled:
        return "mayak_business_connector_disabled"
    if dialog.dialog_kind != "private":
        return "mayak_business_dialog_not_private"
    if (
        canonical.direction != "inbound"
        or canonical.provider_sender_id != dialog.provider_dialog_id
    ):
        return "mayak_business_trigger_invalid"
    if (
        legacy.direction != "inbound"
        or legacy.chat_id != dialog.provider_dialog_id
        or legacy.sender_id != dialog.provider_dialog_id
        or legacy.business_connection_id != connection.business_connection_id
    ):
        return "mayak_business_legacy_binding_invalid"
    if not account.is_active or not account.send_enabled:
        return "mayak_business_account_send_disabled"
    rights = connection.rights if isinstance(connection.rights, dict) else {}
    if not connection.is_enabled or rights.get("can_reply") is not True:
        return "mayak_business_connection_cannot_reply"
    candidate = turn.candidate_text
    if (
        turn.status != "proposed"
        or turn.result_kind != "reply"
        or candidate is None
        or not candidate.strip()
        or len(candidate) > _MAX_TELEGRAM_TEXT_CHARS
        or hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        != grant.candidate_sha256
    ):
        return "mayak_business_candidate_invalid"
    return None


def _terminal_receipt(
    delivery_ref: str,
    attempt: AgentSendAttemptModel,
) -> DemoDeliveryReceipt | None:
    if attempt.state not in _TERMINAL_STATES:
        return None
    state = DemoDeliveryState(attempt.state)
    return DemoDeliveryReceipt(
        delivery_ref,
        state,
        provider_message_ref=attempt.provider_message_id,
        error_code=attempt.error_code,
    )


def _blocked(delivery_ref: str, code: str) -> DemoDeliveryReceipt:
    return DemoDeliveryReceipt(
        delivery_ref,
        DemoDeliveryState.BLOCKED,
        error_code=code,
    )


def _grant_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError("mayak_business_delivery_ref_invalid") from exc


def _provider_message_id(response: dict[str, object]) -> str | None:
    result = response.get("result")
    if response.get("ok") is not True or not isinstance(result, dict):
        return None
    value = result.get("message_id")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _provider_failure(exc: Exception) -> tuple[DemoDeliveryState, str]:
    if isinstance(exc, TelegramApiError):
        if exc.error_code in {400, 403}:
            return DemoDeliveryState.BLOCKED, f"telegram_api_{exc.error_code}"
        if (
            (exc.http_status is not None and exc.http_status >= 500)
            or (exc.error_code is not None and exc.error_code >= 500)
        ):
            return DemoDeliveryState.UNKNOWN, "telegram_provider_unknown"
        return DemoDeliveryState.FAILED, "telegram_api_failed"
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return DemoDeliveryState.UNKNOWN, "telegram_transport_unknown"
    return DemoDeliveryState.UNKNOWN, "telegram_outcome_unconfirmed"
