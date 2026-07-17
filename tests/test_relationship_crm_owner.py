"""Owner projection tests that remain independent from the Gate legacy inbox."""

from datetime import UTC, datetime

import pytest
from app.application.relationship_crm import (
    ConversationAttention,
    ConversationAttentionState,
    RelationshipCrmDomainError,
    SetConversationAttentionCommand,
)
from app.application.relationship_crm_owner import (
    RelationshipOwnerRow,
    RelationshipOwnerScope,
    RelationshipOwnerService,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


class FakeRelationshipOwnerProjectionStore:
    def __init__(self) -> None:
        self.rows: tuple[RelationshipOwnerRow, ...] = ()
        self.attention: ConversationAttention | None = None
        self.calls: list[tuple[str, str, str]] = []

    async def list_rows(
        self,
        *,
        scope: RelationshipOwnerScope,
        attention_state: ConversationAttentionState | None,
        limit: int,
    ) -> tuple[RelationshipOwnerRow, ...]:
        self.calls.append(("list", scope.workspace_key, scope.space_key))
        return self.rows

    async def get_attention(
        self,
        *,
        scope: RelationshipOwnerScope,
        person_key: str,
        conversation_key: str,
    ) -> ConversationAttention | None:
        self.calls.append(("get", scope.workspace_key, scope.space_key))
        return self.attention

    async def save_attention(
        self,
        *,
        scope: RelationshipOwnerScope,
        attention: ConversationAttention,
    ) -> ConversationAttention:
        self.calls.append(("save", scope.workspace_key, scope.space_key))
        self.attention = attention
        return attention


def row(workspace: str = "mark", space: str = "personal") -> RelationshipOwnerRow:
    return RelationshipOwnerRow(
        workspace_key=workspace,
        space_key=space,
        relationship_key="anna-personal",
        person_key="anna",
        display_name="Anna",
        conversation_key="conversation-1",
        attention_state=ConversationAttentionState.UNTRIAGED,
        attention_revision=1,
        updated_at=NOW,
    )


def command(workspace: str = "mark") -> SetConversationAttentionCommand:
    return SetConversationAttentionCommand(
        command_key="attention-command-1",
        workspace_key=workspace,
        person_key="anna",
        conversation_key="conversation-1",
        state=ConversationAttentionState.ACTION_ON_ME,
        expected_revision=0,
        actor_key="owner",
        confirmed=True,
        occurred_at=NOW,
    )


@pytest.mark.anyio
async def test_owner_query_is_bounded_and_explicitly_scoped() -> None:
    store = FakeRelationshipOwnerProjectionStore()
    store.rows = (row(),)
    service = RelationshipOwnerService(store)

    result = await service.list_relationships(
        scope=RelationshipOwnerScope("mark", "personal"),
        limit=10,
    )

    assert result == (row(),)
    assert store.calls == [("list", "mark", "personal")]


@pytest.mark.anyio
async def test_owner_query_rejects_cross_space_repository_leak() -> None:
    store = FakeRelationshipOwnerProjectionStore()
    store.rows = (row(space="commercial"),)
    service = RelationshipOwnerService(store)

    with pytest.raises(
        RelationshipCrmDomainError,
        match="owner_projection_scope_violation",
    ):
        await service.list_relationships(
            scope=RelationshipOwnerScope("mark", "personal")
        )


@pytest.mark.anyio
async def test_attention_write_requires_confirmation_and_workspace_scope() -> None:
    store = FakeRelationshipOwnerProjectionStore()
    service = RelationshipOwnerService(store)
    scope = RelationshipOwnerScope("mark", "personal")

    with pytest.raises(
        RelationshipCrmDomainError,
        match="owner_attention_workspace_mismatch",
    ):
        await service.set_attention(scope=scope, command=command("other"))
    assert store.calls == []


@pytest.mark.anyio
async def test_attention_write_uses_optimistic_domain_command() -> None:
    store = FakeRelationshipOwnerProjectionStore()
    service = RelationshipOwnerService(store)

    saved = await service.set_attention(
        scope=RelationshipOwnerScope("mark", "personal"),
        command=command(),
    )

    assert saved.state is ConversationAttentionState.ACTION_ON_ME
    assert saved.revision == 1
    assert store.calls == [
        ("get", "mark", "personal"),
        ("save", "mark", "personal"),
    ]


def test_display_name_is_not_exposed_by_repr() -> None:
    assert "Anna" not in repr(row())
