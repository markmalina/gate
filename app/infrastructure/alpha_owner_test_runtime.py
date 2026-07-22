"""Bounded runtime for owner-only Alpha test-group presence and delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.alpha_owner_test_policy import (
    OwnerTestPresence,
    OwnerTestPresenceState,
    authorize_owner_test_delivery,
    evaluate_owner_test_presence,
)
from app.application.alpha_shadow_reviews import AlphaShadowReview
from app.core.config import Settings
from app.models.agent import AgentSessionModel, AgentTurnModel
from app.models.alpha import (
    AgentInstallationModel,
    AgentSendAttemptModel,
    DeliveryGrantModel,
)
from app.models.canonical import CanonicalMessage, Dialog, Workspace

JsonObject = dict[str, Any]
ALLOWED_OWNER_TEST_GROUPS = frozenset({"Alpha Group", "Строй αV", "Майя Альфа"})


class AlphaOwnerTestRuntimeError(RuntimeError):
    """Stable error from the bounded owner-test runtime."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def observe_owner_test_presence(
    *,
    session: AsyncSession,
    settings: Settings,
    http_client: httpx.AsyncClient,
    workspace_key: str,
    installation_ref: str,
) -> OwnerTestPresence:
    """Read only aggregate group size and pause whenever evidence is not owner-only."""

    row = (
        await session.execute(
            select(AgentInstallationModel, Dialog)
            .join(
                Dialog,
                (Dialog.id == AgentInstallationModel.dialog_id)
                & (Dialog.workspace_id == AgentInstallationModel.workspace_id),
            )
            .join(Workspace, Workspace.id == AgentInstallationModel.workspace_id)
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                AgentInstallationModel.installation_key == installation_ref,
                AgentInstallationModel.state.in_(("active", "paused")),
            )
        )
    ).one_or_none()
    if row is None:
        raise AlphaOwnerTestRuntimeError("alpha_owner_test_installation_not_found")
    installation, dialog = row
    if installation.dialog_label not in ALLOWED_OWNER_TEST_GROUPS:
        raise AlphaOwnerTestRuntimeError("alpha_owner_test_group_not_authorized")

    observed_at = datetime.now(UTC)
    count = await _telegram_member_count(
        settings=settings,
        http_client=http_client,
        provider_dialog_id=dialog.provider_dialog_id,
    )
    presence = evaluate_owner_test_presence(
        installation_ref=installation.installation_key,
        group_label=installation.dialog_label,
        member_count=count,
        observed_at=observed_at,
    )
    if (
        presence.state is not OwnerTestPresenceState.OWNER_ONLY
        and installation.state == "active"
    ):
        installation.state = "paused"
        installation.updated_at = observed_at
        await session.flush()
    return presence


async def send_owner_test_review(
    *,
    session: AsyncSession,
    settings: Settings,
    http_client: httpx.AsyncClient,
    workspace_key: str,
    installation_ref: str,
    turn_ref: str,
    expected_candidate_sha256: str,
    confirmed: bool,
) -> JsonObject:
    """Recheck presence, issue one exact grant and send at most once."""

    try:
        turn_id = UUID(turn_ref)
    except ValueError as exc:
        raise AlphaOwnerTestRuntimeError("alpha_owner_test_turn_invalid") from exc

    row = (
        await session.execute(
            select(
                AgentInstallationModel,
                Dialog,
                AgentTurnModel,
                CanonicalMessage,
            )
            .join(
                Dialog,
                (Dialog.id == AgentInstallationModel.dialog_id)
                & (Dialog.workspace_id == AgentInstallationModel.workspace_id),
            )
            .join(
                AgentSessionModel,
                (AgentSessionModel.dialog_id == AgentInstallationModel.dialog_id)
                & (AgentSessionModel.workspace_id == AgentInstallationModel.workspace_id),
            )
            .join(
                AgentTurnModel,
                (AgentTurnModel.session_id == AgentSessionModel.id)
                & (AgentTurnModel.workspace_id == AgentSessionModel.workspace_id),
            )
            .join(
                CanonicalMessage,
                (CanonicalMessage.id == AgentTurnModel.trigger_message_id)
                & (CanonicalMessage.workspace_id == AgentTurnModel.workspace_id),
            )
            .join(Workspace, Workspace.id == AgentInstallationModel.workspace_id)
            .where(
                Workspace.key == workspace_key,
                Workspace.is_active.is_(True),
                AgentInstallationModel.installation_key == installation_ref,
                AgentTurnModel.id == turn_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise AlphaOwnerTestRuntimeError("alpha_owner_test_review_not_found")
    installation, dialog, turn, trigger = row

    existing = (
        await session.execute(
            select(DeliveryGrantModel, AgentSendAttemptModel)
            .join(
                AgentSendAttemptModel,
                (AgentSendAttemptModel.grant_id == DeliveryGrantModel.id)
                & (AgentSendAttemptModel.workspace_id == DeliveryGrantModel.workspace_id),
            )
            .where(
                DeliveryGrantModel.workspace_id == installation.workspace_id,
                DeliveryGrantModel.installation_id == installation.id,
                DeliveryGrantModel.trigger_message_id == turn.trigger_message_id,
                DeliveryGrantModel.candidate_sha256 == expected_candidate_sha256,
            )
        )
    ).one_or_none()
    if existing is not None:
        _, attempt = existing
        return _delivery_payload(
            installation_ref=installation.installation_key,
            turn_ref=str(turn.id),
            state=attempt.state,
            replay=True,
        )

    presence = await observe_owner_test_presence(
        session=session,
        settings=settings,
        http_client=http_client,
        workspace_key=workspace_key,
        installation_ref=installation_ref,
    )
    review = AlphaShadowReview(
        turn_ref=str(turn.id),
        installation_ref=installation.installation_key,
        group_label=installation.dialog_label,
        deployment_key="alpha-model-v2-openai",
        status=turn.status,
        outcome=turn.result_kind,
        result_code=turn.result_code,
        trigger_text=trigger.text_content,
        input_origin=trigger.input_origin,
        candidate_text=turn.candidate_text,
        candidate_sha256=turn.candidate_sha256,
        context_message_count=len(turn.context_message_ids),
        created_at=turn.created_at,
    )
    now = datetime.now(UTC)
    grant_request = authorize_owner_test_delivery(
        review=review,
        presence=presence,
        allowed_group_labels=ALLOWED_OWNER_TEST_GROUPS,
        expected_candidate_sha256=expected_candidate_sha256,
        grant_key=f"owner-test:{turn.id}:{expected_candidate_sha256[:12]}",
        now=now,
        confirmed=confirmed,
    )
    grant = DeliveryGrantModel(
        workspace_id=installation.workspace_id,
        installation_id=installation.id,
        trigger_message_id=turn.trigger_message_id,
        grant_key=grant_request.grant_key,
        candidate_sha256=grant_request.candidate_sha256,
        expires_at=grant_request.expires_at,
        used_at=now,
    )
    session.add(grant)
    await session.flush()
    attempt = AgentSendAttemptModel(
        workspace_id=installation.workspace_id,
        grant_id=grant.id,
        state="sending",
    )
    session.add(attempt)
    await session.flush()

    token = settings.telegram_alpha_bot_token.get_secret_value().strip()
    if not token:
        attempt.state = "blocked"
        attempt.error_code = "alpha_owner_test_credentials_incomplete"
        await session.flush()
        return _delivery_payload(
            installation_ref=installation.installation_key,
            turn_ref=str(turn.id),
            state=attempt.state,
            replay=False,
        )

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = await http_client.post(
            endpoint,
            json={"chat_id": dialog.provider_dialog_id, "text": turn.candidate_text},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        attempt.state = "unknown"
        attempt.error_code = "alpha_owner_test_provider_outcome_unknown"
    else:
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            attempt.state = "failed"
            attempt.error_code = "alpha_owner_test_provider_rejected"
        elif not isinstance(result, dict):
            attempt.state = "unknown"
            attempt.error_code = "alpha_owner_test_provider_result_invalid"
        else:
            attempt.state = "sent"
            message_id = result.get("message_id")
            if isinstance(message_id, int) and not isinstance(message_id, bool):
                attempt.provider_message_id = str(message_id)
    attempt.updated_at = datetime.now(UTC)
    await session.flush()
    return _delivery_payload(
        installation_ref=installation.installation_key,
        turn_ref=str(turn.id),
        state=attempt.state,
        replay=False,
    )


async def _telegram_member_count(
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    provider_dialog_id: str,
) -> int | None:
    token = settings.telegram_alpha_bot_token.get_secret_value().strip()
    if not token:
        return None
    endpoint = f"https://api.telegram.org/bot{token}/getChatMemberCount"
    try:
        response = await http_client.post(
            endpoint,
            json={"chat_id": provider_dialog_id},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or not isinstance(result, int)
        or isinstance(result, bool)
        or result < 0
    ):
        return None
    return result


def _delivery_payload(
    *,
    installation_ref: str,
    turn_ref: str,
    state: str,
    replay: bool,
) -> JsonObject:
    return {
        "installation_ref": installation_ref,
        "turn_ref": turn_ref,
        "state": state,
        "replay": replay,
        "provider_message_id_exposed": False,
        "participant_identities_exposed": False,
    }
