"""Application-service tests for mandatory Relationship CRM scope."""

from datetime import UTC, datetime

import pytest

from app.application.relationship_crm import (
    InteractionDirection,
    PersonRecord,
    RelationshipCrmDomainError,
    RelationshipInteraction,
    RelationshipInteractionKind,
    RelationshipRecord,
    RelationshipRole,
    RelationshipSpace,
    RelationshipSpaceKind,
)
from app.application.relationship_crm_repository import (
    RelationshipCrmCoreService,
    RelationshipCrmScope,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


class FakeRelationshipCrmRepository:
    def __init__(self) -> None:
        self.spaces: dict[tuple[str, str], RelationshipSpace] = {}
        self.people: dict[tuple[str, str], PersonRecord] = {}
        self.relationships: dict[tuple[str, str, str], RelationshipRecord] = {}
        self.interactions: dict[
            tuple[str, str, str],
            list[RelationshipInteraction],
        ] = {}
        self.calls: list[tuple[str, str, str]] = []

    async def get_space(self, scope: RelationshipCrmScope) -> RelationshipSpace | None:
        self.calls.append(("get_space", scope.workspace_key, scope.space_key))
        return self.spaces.get((scope.workspace_key, scope.space_key))

    async def get_person(
        self,
        *,
        workspace_key: str,
        person_key: str,
    ) -> PersonRecord | None:
        self.calls.append(("get_person", workspace_key, person_key))
        return self.people.get((workspace_key, person_key))

    async def get_relationship(
        self,
        *,
        scope: RelationshipCrmScope,
        relationship_key: str,
    ) -> RelationshipRecord | None:
        self.calls.append(("get_relationship", scope.workspace_key, scope.space_key))
        return self.relationships.get(
            (scope.workspace_key, scope.space_key, relationship_key)
        )

    async def add_space(self, space: RelationshipSpace) -> RelationshipSpace:
        self.spaces[(space.workspace_key, space.space_key)] = space
        return space

    async def add_person(self, person: PersonRecord) -> PersonRecord:
        self.people[(person.workspace_key, person.person_key)] = person
        return person

    async def add_relationship(
        self,
        relationship: RelationshipRecord,
    ) -> RelationshipRecord:
        key = (
            relationship.workspace_key,
            relationship.space_key,
            relationship.relationship_key,
        )
        self.relationships[key] = relationship
        return relationship

    async def add_interaction(
        self,
        interaction: RelationshipInteraction,
    ) -> RelationshipInteraction:
        key = (
            interaction.workspace_key,
            interaction.space_key,
            interaction.relationship_key,
        )
        self.interactions.setdefault(key, []).append(interaction)
        return interaction

    async def list_interactions(
        self,
        *,
        scope: RelationshipCrmScope,
        relationship_key: str,
        limit: int,
    ) -> tuple[RelationshipInteraction, ...]:
        self.calls.append(("list_interactions", scope.workspace_key, scope.space_key))
        key = (scope.workspace_key, scope.space_key, relationship_key)
        return tuple(self.interactions.get(key, [])[-limit:])


def space(workspace: str = "mark") -> RelationshipSpace:
    return RelationshipSpace(
        workspace_key=workspace,
        space_key="personal",
        kind=RelationshipSpaceKind.PERSONAL,
        name="Personal",
        created_at=NOW,
    )


def person(workspace: str = "mark") -> PersonRecord:
    return PersonRecord(
        workspace_key=workspace,
        person_key="anna",
        display_name="Anna",
        created_at=NOW,
    )


def relationship(workspace: str = "mark") -> RelationshipRecord:
    return RelationshipRecord(
        workspace_key=workspace,
        space_key="personal",
        space_kind=RelationshipSpaceKind.PERSONAL,
        relationship_key="anna-personal",
        person_key="anna",
        role=RelationshipRole.FRIEND,
        created_at=NOW,
    )


@pytest.mark.anyio
async def test_core_service_registers_explicit_space_person_and_relationship() -> None:
    repository = FakeRelationshipCrmRepository()
    service = RelationshipCrmCoreService(repository)
    scope = RelationshipCrmScope("mark", "personal")

    assert await service.register_space(space()) == space()
    assert await service.register_person(scope=scope, person=person()) == person()
    assert await service.register_relationship(relationship()) == relationship()
    assert ("get_space", "mark", "personal") in repository.calls
    assert ("get_person", "mark", "anna") in repository.calls


@pytest.mark.anyio
async def test_cross_workspace_person_fails_before_repository_write() -> None:
    repository = FakeRelationshipCrmRepository()
    service = RelationshipCrmCoreService(repository)
    await service.register_space(space())

    with pytest.raises(
        RelationshipCrmDomainError,
        match="relationship_person_workspace_mismatch",
    ):
        await service.register_person(
            scope=RelationshipCrmScope("mark", "personal"),
            person=person("other"),
        )
    assert repository.people == {}


@pytest.mark.anyio
async def test_interaction_requires_matching_relationship_and_scope() -> None:
    repository = FakeRelationshipCrmRepository()
    service = RelationshipCrmCoreService(repository)
    scope = RelationshipCrmScope("mark", "personal")
    await service.register_space(space())
    await service.register_person(scope=scope, person=person())
    await service.register_relationship(relationship())

    interaction = RelationshipInteraction(
        workspace_key="mark",
        space_key="personal",
        relationship_key="anna-personal",
        person_key="anna",
        interaction_key="message-1",
        kind=RelationshipInteractionKind.MESSAGE,
        direction=InteractionDirection.INBOUND,
        occurred_at=NOW,
        summary="Hello",
    )
    assert await service.record_interaction(interaction) == interaction
    assert await service.timeline(
        scope=scope,
        relationship_key="anna-personal",
    ) == (interaction,)


@pytest.mark.anyio
async def test_timeline_rejects_repository_scope_leak() -> None:
    repository = FakeRelationshipCrmRepository()
    service = RelationshipCrmCoreService(repository)
    scope = RelationshipCrmScope("mark", "personal")
    await service.register_space(space())
    await service.register_person(scope=scope, person=person())
    await service.register_relationship(relationship())

    leaked = RelationshipInteraction(
        workspace_key="other",
        space_key="personal",
        relationship_key="anna-personal",
        person_key="anna",
        interaction_key="message-leaked",
        kind=RelationshipInteractionKind.MESSAGE,
        direction=InteractionDirection.INBOUND,
        occurred_at=NOW,
    )
    repository.interactions[("mark", "personal", "anna-personal")] = [leaked]

    with pytest.raises(
        RelationshipCrmDomainError,
        match="relationship_repository_scope_violation",
    ):
        await service.timeline(
            scope=scope,
            relationship_key="anna-personal",
        )


@pytest.mark.anyio
async def test_timeline_limit_is_bounded() -> None:
    service = RelationshipCrmCoreService(FakeRelationshipCrmRepository())
    with pytest.raises(
        RelationshipCrmDomainError,
        match="relationship_timeline_limit_invalid",
    ):
        await service.timeline(
            scope=RelationshipCrmScope("mark", "personal"),
            relationship_key="anna-personal",
            limit=201,
        )
