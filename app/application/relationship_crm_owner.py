"""Owner read/write projections for Relationship CRM, separate from the legacy inbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.application.relationship_crm import (
    ConversationAttention,
    ConversationAttentionState,
    RelationshipCrmDomainError,
    SetConversationAttentionCommand,
    apply_attention_command,
)


@dataclass(frozen=True, slots=True)
class RelationshipOwnerScope:
    workspace_key: str
    space_key: str

    def __post_init__(self) -> None:
        _token(self.workspace_key, "owner_workspace_key")
        _token(self.space_key, "owner_space_key")


@dataclass(frozen=True, slots=True)
class RelationshipOwnerRow:
    workspace_key: str
    space_key: str
    relationship_key: str
    person_key: str
    display_name: str = field(repr=False)
    conversation_key: str
    attention_state: ConversationAttentionState
    attention_revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.workspace_key, "owner_workspace_key"),
            (self.space_key, "owner_space_key"),
            (self.relationship_key, "owner_relationship_key"),
            (self.person_key, "owner_person_key"),
            (self.conversation_key, "owner_conversation_key"),
        ):
            _token(value, name)
        if not self.display_name.strip() or len(self.display_name) > 255:
            raise RelationshipCrmDomainError("owner_display_name_invalid")
        if self.attention_revision < 1:
            raise RelationshipCrmDomainError("owner_attention_revision_invalid")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise RelationshipCrmDomainError("owner_updated_at_timezone_required")


class RelationshipOwnerProjectionStore(Protocol):
    async def list_rows(
        self,
        *,
        scope: RelationshipOwnerScope,
        attention_state: ConversationAttentionState | None,
        limit: int,
    ) -> tuple[RelationshipOwnerRow, ...]: ...

    async def get_attention(
        self,
        *,
        scope: RelationshipOwnerScope,
        person_key: str,
        conversation_key: str,
    ) -> ConversationAttention | None: ...

    async def save_attention(
        self,
        *,
        scope: RelationshipOwnerScope,
        attention: ConversationAttention,
    ) -> ConversationAttention: ...


class RelationshipOwnerService:
    """Bounded owner queries plus explicit, confirmed attention commands."""

    def __init__(self, store: RelationshipOwnerProjectionStore) -> None:
        self._store = store

    async def list_relationships(
        self,
        *,
        scope: RelationshipOwnerScope,
        attention_state: ConversationAttentionState | None = None,
        limit: int = 50,
    ) -> tuple[RelationshipOwnerRow, ...]:
        if limit < 1 or limit > 200:
            raise RelationshipCrmDomainError("owner_projection_limit_invalid")
        rows = await self._store.list_rows(
            scope=scope,
            attention_state=attention_state,
            limit=limit,
        )
        if len(rows) > limit:
            raise RelationshipCrmDomainError("owner_projection_limit_violation")
        if any(
            row.workspace_key != scope.workspace_key
            or row.space_key != scope.space_key
            or (
                attention_state is not None
                and row.attention_state is not attention_state
            )
            for row in rows
        ):
            raise RelationshipCrmDomainError("owner_projection_scope_violation")
        return rows

    async def set_attention(
        self,
        *,
        scope: RelationshipOwnerScope,
        command: SetConversationAttentionCommand,
    ) -> ConversationAttention:
        if command.workspace_key != scope.workspace_key:
            raise RelationshipCrmDomainError("owner_attention_workspace_mismatch")
        current = await self._store.get_attention(
            scope=scope,
            person_key=command.person_key,
            conversation_key=command.conversation_key,
        )
        updated = apply_attention_command(current, command)
        saved = await self._store.save_attention(scope=scope, attention=updated)
        if (
            saved != updated
            or saved.workspace_key != scope.workspace_key
            or saved.person_key != command.person_key
            or saved.conversation_key != command.conversation_key
        ):
            raise RelationshipCrmDomainError("owner_attention_store_violation")
        return saved


def _token(value: str, field_name: str) -> None:
    if not value or value != value.strip() or len(value) > 128:
        raise RelationshipCrmDomainError(f"{field_name}_invalid")
