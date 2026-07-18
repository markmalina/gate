"""Unit guards for canonical ingress persistence boundaries."""

import asyncio
from datetime import UTC, datetime

import pytest
from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    CanonicalMessageEnvelope,
    ConnectorKind,
    DialogIdentity,
    DialogKind,
    WorkOwnershipDecision,
    WorkRoute,
)
from app.application.work_ownership import WorkOwnershipState
from app.infrastructure.canonical_messaging import SqlCanonicalIngressStore


class ExplodingSession:
    async def scalar(self, statement: object) -> object:
        raise AssertionError("invalid scope must fail before SQL")


def _dialog(*, workspace_id: str = "mark-personal") -> DialogIdentity:
    return DialogIdentity(
        workspace_id=workspace_id,
        connector_key="telegram_business",
        connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
        provider_dialog_id="private-dialog",
        dialog_kind=DialogKind.PRIVATE,
    )


def _message(*, workspace_id: str = "mark-personal") -> CanonicalMessageEnvelope:
    return CanonicalMessageEnvelope(
        workspace_id=workspace_id,
        connector_key="telegram_business",
        source_receipt_id="00000000-0000-0000-0000-000000000001",
        provider_dialog_id="private-dialog",
        provider_message_id="provider-message",
        provider_sender_id="provider-sender",
        direction=CanonicalMessageDirection.INBOUND,
        occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
        text_present=True,
        text_content="private-message-text",
    )


def _ownership() -> WorkOwnershipDecision:
    return WorkOwnershipDecision(
        state=WorkOwnershipState.HUMAN_MANAGED,
        route=WorkRoute.HUMAN_REPLY_TASK,
        policy_version="ownership-v1",
        reason_code="human_inbound",
    )


def test_store_rejects_cross_workspace_input_before_sql() -> None:
    store = SqlCanonicalIngressStore(ExplodingSession())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="workspace scope mismatch"):
        asyncio.run(
            store.persist(
                dialog=_dialog(workspace_id="workspace-a"),
                message=_message(workspace_id="workspace-b"),
                ownership=_ownership(),
            )
        )


def test_store_rejects_invalid_receipt_before_sql() -> None:
    store = SqlCanonicalIngressStore(ExplodingSession())  # type: ignore[arg-type]
    message = _message()
    invalid = CanonicalMessageEnvelope(
        workspace_id=message.workspace_id,
        connector_key=message.connector_key,
        source_receipt_id="not-a-uuid",
        provider_dialog_id=message.provider_dialog_id,
        provider_message_id=message.provider_message_id,
        provider_sender_id=message.provider_sender_id,
        direction=message.direction,
        occurred_at=message.occurred_at,
        text_present=message.text_present,
        text_content=message.text_content,
        input_origin=message.input_origin,
    )

    with pytest.raises(ValueError, match="source_receipt_id must be a UUID"):
        asyncio.run(
            store.persist(
                dialog=_dialog(),
                message=invalid,
                ownership=_ownership(),
            )
        )
