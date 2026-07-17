"""Scoped repository port and application service for Relationship CRM core facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.relationship_crm import (
    PersonRecord,
    RelationshipCrmDomainError,
    RelationshipInteraction,
    RelationshipRecord,
    RelationshipSpace,
)


@dataclass(frozen=True, slots=True)
class RelationshipCrmScope:
    """Required privacy boundary for every Relationship CRM repository operation."""

    workspace_key: str
    space_key: str

    def __post_init__(self) -> None:
        _token(self.workspace_key, "workspace_key")
        _token(self.space_key, "space_key")


class RelationshipCrmRepository(Protocol):
    """Persistence port. Implementations must include both scope keys in every query."""

    async def get_space(self, scope: RelationshipCrmScope) -> RelationshipSpace | None: ...

    async def get_person(
        self,
        *,
        workspace_key: str,
        person_key: str,
    ) -> PersonRecord | None: ...

    async def get_relationship(
        self,
        *,
        scope: RelationshipCrmScope,
        relationship_key: str,
    ) -> RelationshipRecord | None: ...

    async def add_space(self, space: RelationshipSpace) -> RelationshipSpace: ...

    async def add_person(self, person: PersonRecord) -> PersonRecord: ...

    async def add_relationship(
        self,
        relationship: RelationshipRecord,
    ) -> RelationshipRecord: ...

    async def add_interaction(
        self,
        interaction: RelationshipInteraction,
    ) -> RelationshipInteraction: ...

    async def list_interactions(
        self,
        *,
        scope: RelationshipCrmScope,
        relationship_key: str,
        limit: int,
    ) -> tuple[RelationshipInteraction, ...]: ...


class RelationshipCrmCoreService:
    """Coordinates core writes without guessing identity, attention or commercial state."""

    def __init__(self, repository: RelationshipCrmRepository) -> None:
        self._repository = repository

    async def register_space(self, space: RelationshipSpace) -> RelationshipSpace:
        scope = RelationshipCrmScope(space.workspace_key, space.space_key)
        existing = await self._repository.get_space(scope)
        if existing is not None:
            if existing != space:
                raise RelationshipCrmDomainError("relationship_space_conflict")
            return existing
        return await self._repository.add_space(space)

    async def register_person(
        self,
        *,
        scope: RelationshipCrmScope,
        person: PersonRecord,
    ) -> PersonRecord:
        if person.workspace_key != scope.workspace_key:
            raise RelationshipCrmDomainError("relationship_person_workspace_mismatch")
        existing = await self._repository.get_person(
            workspace_key=scope.workspace_key,
            person_key=person.person_key,
        )
        if existing is not None:
            if existing != person:
                raise RelationshipCrmDomainError("relationship_person_conflict")
            return existing
        await self.require_space(scope)
        return await self._repository.add_person(person)

    async def register_relationship(
        self,
        relationship: RelationshipRecord,
    ) -> RelationshipRecord:
        scope = RelationshipCrmScope(
            relationship.workspace_key,
            relationship.space_key,
        )
        space = await self.require_space(scope)
        if relationship.space_kind is not space.kind:
            raise RelationshipCrmDomainError("relationship_space_kind_mismatch")
        person = await self._repository.get_person(
            workspace_key=scope.workspace_key,
            person_key=relationship.person_key,
        )
        if person is None:
            raise RelationshipCrmDomainError("relationship_person_not_found")
        existing = await self._repository.get_relationship(
            scope=scope,
            relationship_key=relationship.relationship_key,
        )
        if existing is not None:
            if existing != relationship:
                raise RelationshipCrmDomainError("relationship_record_conflict")
            return existing
        return await self._repository.add_relationship(relationship)

    async def record_interaction(
        self,
        interaction: RelationshipInteraction,
    ) -> RelationshipInteraction:
        scope = RelationshipCrmScope(
            interaction.workspace_key,
            interaction.space_key,
        )
        relationship = await self._repository.get_relationship(
            scope=scope,
            relationship_key=interaction.relationship_key,
        )
        if relationship is None:
            raise RelationshipCrmDomainError("relationship_not_found")
        if relationship.person_key != interaction.person_key:
            raise RelationshipCrmDomainError("relationship_interaction_person_mismatch")
        return await self._repository.add_interaction(interaction)

    async def timeline(
        self,
        *,
        scope: RelationshipCrmScope,
        relationship_key: str,
        limit: int = 50,
    ) -> tuple[RelationshipInteraction, ...]:
        if limit < 1 or limit > 200:
            raise RelationshipCrmDomainError("relationship_timeline_limit_invalid")
        relationship = await self._repository.get_relationship(
            scope=scope,
            relationship_key=relationship_key,
        )
        if relationship is None:
            raise RelationshipCrmDomainError("relationship_not_found")
        records = await self._repository.list_interactions(
            scope=scope,
            relationship_key=relationship_key,
            limit=limit,
        )
        if any(
            item.workspace_key != scope.workspace_key
            or item.space_key != scope.space_key
            or item.relationship_key != relationship_key
            for item in records
        ):
            raise RelationshipCrmDomainError("relationship_repository_scope_violation")
        return records

    async def require_space(self, scope: RelationshipCrmScope) -> RelationshipSpace:
        space = await self._repository.get_space(scope)
        if space is None:
            raise RelationshipCrmDomainError("relationship_space_not_found")
        if (
            space.workspace_key != scope.workspace_key
            or space.space_key != scope.space_key
        ):
            raise RelationshipCrmDomainError("relationship_repository_scope_violation")
        return space


def _token(value: str, field_name: str) -> None:
    if not value or value != value.strip() or len(value) > 128:
        raise RelationshipCrmDomainError(f"{field_name}_invalid")
