"""PostgreSQL adapter for atomic canonical ingress persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.canonical_messaging import (
    CanonicalIngressResult,
    CanonicalIngressStore,
    CanonicalMessageEnvelope,
    DialogIdentity,
    WorkOwnershipDecision,
)
from app.models.canonical import (
    CanonicalMessage,
    Dialog,
    TransportConnector,
    WorkOwnership,
    Workspace,
)


class CanonicalIngressConflictError(RuntimeError):
    """A stable idempotency identity was reused with incompatible facts."""


class SqlCanonicalIngressStore(CanonicalIngressStore):
    """Persist one canonical fact set inside the caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist(
        self,
        *,
        dialog: DialogIdentity,
        message: CanonicalMessageEnvelope,
        ownership: WorkOwnershipDecision,
    ) -> CanonicalIngressResult:
        _validate_scope(dialog=dialog, message=message)
        receipt_id = _uuid(message.source_receipt_id, field_name="source_receipt_id")
        workspace_id = await self._workspace_id(dialog.workspace_id)
        connector_id = await self._connector_id(
            workspace_id=workspace_id,
            connector_key=dialog.connector_key,
            connector_kind=dialog.connector_kind.value,
        )
        dialog_id = await self._dialog_id(
            workspace_id=workspace_id,
            connector_id=connector_id,
            dialog=dialog,
        )
        message_id, duplicate = await self._message_id(
            workspace_id=workspace_id,
            dialog_id=dialog_id,
            receipt_id=receipt_id,
            message=message,
        )
        ownership_id = await self._ownership_id(
            workspace_id=workspace_id,
            dialog_id=dialog_id,
            message_id=message_id,
            ownership=ownership,
            update_current=not duplicate,
        )
        return CanonicalIngressResult(
            dialog_id=str(dialog_id),
            message_id=str(message_id),
            ownership_id=str(ownership_id),
            duplicate=duplicate,
        )

    async def _workspace_id(self, key: str) -> uuid.UUID:
        created = await self._session.scalar(
            insert(Workspace)
            .values(key=key, display_name=key, is_active=True)
            .on_conflict_do_nothing(index_elements=[Workspace.key])
            .returning(Workspace.id)
        )
        if created is not None:
            return created
        existing = await self._session.scalar(select(Workspace.id).where(Workspace.key == key))
        if existing is None:
            raise CanonicalIngressConflictError("workspace_resolution_failed")
        return existing

    async def _connector_id(
        self,
        *,
        workspace_id: uuid.UUID,
        connector_key: str,
        connector_kind: str,
    ) -> uuid.UUID:
        created = await self._session.scalar(
            insert(TransportConnector)
            .values(
                workspace_id=workspace_id,
                connector_key=connector_key,
                connector_kind=connector_kind,
                is_enabled=False,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    TransportConnector.workspace_id,
                    TransportConnector.connector_key,
                ]
            )
            .returning(TransportConnector.id)
        )
        if created is not None:
            return created
        result = await self._session.execute(
            select(TransportConnector.id, TransportConnector.connector_kind).where(
                TransportConnector.workspace_id == workspace_id,
                TransportConnector.connector_key == connector_key,
            )
        )
        existing = result.tuples().one_or_none()
        if existing is None or existing[1] != connector_kind:
            raise CanonicalIngressConflictError("connector_identity_conflict")
        return existing[0]

    async def _dialog_id(
        self,
        *,
        workspace_id: uuid.UUID,
        connector_id: uuid.UUID,
        dialog: DialogIdentity,
    ) -> uuid.UUID:
        created = await self._session.scalar(
            insert(Dialog)
            .values(
                workspace_id=workspace_id,
                connector_id=connector_id,
                provider_dialog_id=dialog.provider_dialog_id,
                dialog_kind=dialog.dialog_kind.value,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    Dialog.workspace_id,
                    Dialog.connector_id,
                    Dialog.provider_dialog_id,
                ]
            )
            .returning(Dialog.id)
        )
        if created is not None:
            return created
        result = await self._session.execute(
            select(Dialog.id, Dialog.dialog_kind).where(
                Dialog.workspace_id == workspace_id,
                Dialog.connector_id == connector_id,
                Dialog.provider_dialog_id == dialog.provider_dialog_id,
            )
        )
        existing = result.tuples().one_or_none()
        if existing is None or existing[1] != dialog.dialog_kind.value:
            raise CanonicalIngressConflictError("dialog_identity_conflict")
        return existing[0]

    async def _message_id(
        self,
        *,
        workspace_id: uuid.UUID,
        dialog_id: uuid.UUID,
        receipt_id: uuid.UUID,
        message: CanonicalMessageEnvelope,
    ) -> tuple[uuid.UUID, bool]:
        occurred_at = message.occurred_at
        if occurred_at is None or occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        created = await self._session.scalar(
            insert(CanonicalMessage)
            .values(
                workspace_id=workspace_id,
                dialog_id=dialog_id,
                receipt_id=receipt_id,
                provider_message_id=message.provider_message_id,
                provider_sender_id=message.provider_sender_id,
                direction=message.direction.value,
                text_content=None,
                occurred_at=occurred_at,
            )
            .on_conflict_do_nothing()
            .returning(CanonicalMessage.id)
        )
        if created is not None:
            return created, False
        existing = (
            await self._session.execute(
                select(
                    CanonicalMessage.id,
                    CanonicalMessage.dialog_id,
                    CanonicalMessage.provider_message_id,
                    CanonicalMessage.provider_sender_id,
                    CanonicalMessage.direction,
                    CanonicalMessage.occurred_at,
                ).where(
                    or_(
                        CanonicalMessage.receipt_id == receipt_id,
                        and_(
                            CanonicalMessage.dialog_id == dialog_id,
                            CanonicalMessage.provider_message_id
                            == message.provider_message_id,
                        ),
                    )
                )
            )
        ).one_or_none()
        expected = (
            dialog_id,
            message.provider_message_id,
            message.provider_sender_id,
            message.direction.value,
            occurred_at,
        )
        if existing is None or tuple(existing[1:]) != expected:
            raise CanonicalIngressConflictError("message_identity_conflict")
        return existing.id, True

    async def _ownership_id(
        self,
        *,
        workspace_id: uuid.UUID,
        dialog_id: uuid.UUID,
        message_id: uuid.UUID,
        ownership: WorkOwnershipDecision,
        update_current: bool,
    ) -> uuid.UUID:
        agent_key = (
            ownership.candidate_agent.value
            if ownership.candidate_agent is not None
            else None
        )
        values = {
            "workspace_id": workspace_id,
            "dialog_id": dialog_id,
            "last_message_id": message_id,
            "version": 1,
            "state": ownership.state.value,
            "route": ownership.route.value,
            "agent_key": agent_key,
            "policy_version": ownership.policy_version,
            "reason_code": ownership.reason_code,
        }
        statement = insert(WorkOwnership).values(**values)
        if update_current:
            statement = statement.on_conflict_do_update(
                index_elements=[WorkOwnership.dialog_id],
                set_={
                    "last_message_id": message_id,
                    "version": WorkOwnership.version + 1,
                    "state": ownership.state.value,
                    "route": ownership.route.value,
                    "agent_key": agent_key,
                    "policy_version": ownership.policy_version,
                    "reason_code": ownership.reason_code,
                },
            )
        else:
            statement = statement.on_conflict_do_nothing(
                index_elements=[WorkOwnership.dialog_id]
            )
        persisted = await self._session.scalar(statement.returning(WorkOwnership.id))
        if persisted is not None:
            return persisted
        existing = await self._session.scalar(
            select(WorkOwnership.id).where(WorkOwnership.dialog_id == dialog_id)
        )
        if existing is None:
            raise CanonicalIngressConflictError("ownership_resolution_failed")
        return existing


class SessionFactoryCanonicalIngressStore(CanonicalIngressStore):
    """Commit canonical facts in a short transaction independent from queue state."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def persist(
        self,
        *,
        dialog: DialogIdentity,
        message: CanonicalMessageEnvelope,
        ownership: WorkOwnershipDecision,
    ) -> CanonicalIngressResult:
        async with self._sessions.begin() as session:
            return await SqlCanonicalIngressStore(session).persist(
                dialog=dialog,
                message=message,
                ownership=ownership,
            )


def _validate_scope(
    *,
    dialog: DialogIdentity,
    message: CanonicalMessageEnvelope,
) -> None:
    if dialog.workspace_id != message.workspace_id:
        raise ValueError("workspace scope mismatch")
    if dialog.connector_key != message.connector_key:
        raise ValueError("connector scope mismatch")
    if dialog.provider_dialog_id != message.provider_dialog_id:
        raise ValueError("dialog identity mismatch")


def _uuid(value: str, *, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc
