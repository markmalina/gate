"""SIMULATED PostgreSQL persistence proofs for role-correct BECOME Quests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from app.infrastructure.become_quest import (
    SqlBecomeQuestError,
    SqlBecomeQuestRepository,
    become_quest_status,
)
from app.models.become_quest import (
    BecomeQuestModel,
    BecomeQuestOperationAttemptModel,
    BecomeQuestParticipantModel,
)
from app.models.canonical import Workspace
from app.models.domain import Base
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
QUEST_ONE = "ref://simulated/quest/postgres-one"
CORRELATION_ONE = "ref://simulated/correlation/postgres-one"
EXPERT = "ref://simulated/person/lesha"
LEARNER = "ref://simulated/person/mark"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def simulated_postgres_sessions() -> AsyncIterator[
    async_sessionmaker[AsyncSession]
]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for simulated Quest persistence tests")
    schema = f"become_quest_test_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def seed_simulated_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session, session.begin():
        session.add(
            Workspace(
                key="become-simulated",
                display_name="BECOME SIMULATED",
                is_active=True,
            )
        )


@pytest.mark.anyio
async def test_simulated_quest_and_roles_survive_repository_restart() -> None:
    async with simulated_postgres_sessions() as sessions:
        await seed_simulated_workspace(sessions)
        first_process = SqlBecomeQuestRepository(sessions)
        identity = await first_process.create_quest(
            workspace_key="become-simulated",
            quest_ref=QUEST_ONE,
            correlation_ref=CORRELATION_ONE,
            mode="SIMULATED",
            source_provenance_digest=DIGEST_A,
        )
        assert (
            await first_process.bind_participant(
                quest_ref=QUEST_ONE,
                correlation_ref=CORRELATION_ONE,
                role="EXPERT",
                person_ref=EXPERT,
                provenance_digest=DIGEST_A,
            )
            == "created"
        )
        assert (
            await first_process.bind_participant(
                quest_ref=QUEST_ONE,
                correlation_ref=CORRELATION_ONE,
                role="LEARNER",
                person_ref=LEARNER,
                provenance_digest=DIGEST_B,
            )
            == "created"
        )

        restarted_process = SqlBecomeQuestRepository(sessions)
        replayed = await restarted_process.create_quest(
            workspace_key="become-simulated",
            quest_ref=QUEST_ONE,
            correlation_ref=CORRELATION_ONE,
            mode="SIMULATED",
            source_provenance_digest=DIGEST_A,
        )
        async with sessions() as session:
            quest = await session.get(BecomeQuestModel, identity.quest_id)
            bindings = (
                await session.scalars(
                    select(BecomeQuestParticipantModel).where(
                        BecomeQuestParticipantModel.quest_id == identity.quest_id
                    )
                )
            ).all()

        assert replayed == identity
        assert quest is not None
        assert quest.expert_person_ref == EXPERT
        assert quest.learner_person_ref == LEARNER
        assert {(binding.role, binding.person_ref) for binding in bindings} == {
            ("EXPERT", EXPERT),
            ("LEARNER", LEARNER),
        }


@pytest.mark.anyio
async def test_simulated_role_replay_is_exact_and_fail_closed() -> None:
    async with simulated_postgres_sessions() as sessions:
        await seed_simulated_workspace(sessions)
        repository = SqlBecomeQuestRepository(sessions)
        await repository.create_quest(
            workspace_key="become-simulated",
            quest_ref=QUEST_ONE,
            correlation_ref=CORRELATION_ONE,
            mode="SIMULATED",
            source_provenance_digest=DIGEST_A,
        )
        await repository.bind_participant(
            quest_ref=QUEST_ONE,
            correlation_ref=CORRELATION_ONE,
            role="EXPERT",
            person_ref=EXPERT,
            provenance_digest=DIGEST_A,
        )

        assert (
            await repository.bind_participant(
                quest_ref=QUEST_ONE,
                correlation_ref=CORRELATION_ONE,
                role="EXPERT",
                person_ref=EXPERT,
                provenance_digest=DIGEST_A,
            )
            == "already_current"
        )
        with pytest.raises(SqlBecomeQuestError, match="role_rebinding_forbidden"):
            await repository.bind_participant(
                quest_ref=QUEST_ONE,
                correlation_ref=CORRELATION_ONE,
                role="EXPERT",
                person_ref=LEARNER,
                provenance_digest=DIGEST_A,
            )
        with pytest.raises(
            SqlBecomeQuestError,
            match="roles_must_use_distinct_people",
        ):
            await repository.bind_participant(
                quest_ref=QUEST_ONE,
                correlation_ref=CORRELATION_ONE,
                role="LEARNER",
                person_ref=EXPERT,
                provenance_digest=DIGEST_B,
            )


@pytest.mark.anyio
async def test_simulated_attempts_are_scoped_per_quest_and_correlation() -> None:
    async with simulated_postgres_sessions() as sessions:
        await seed_simulated_workspace(sessions)
        repository = SqlBecomeQuestRepository(sessions)
        await repository.create_quest(
            workspace_key="become-simulated",
            quest_ref=QUEST_ONE,
            correlation_ref=CORRELATION_ONE,
            mode="SIMULATED",
            source_provenance_digest=DIGEST_A,
        )
        await repository.create_quest(
            workspace_key="become-simulated",
            quest_ref="ref://simulated/quest/postgres-two",
            correlation_ref="ref://simulated/correlation/postgres-two",
            mode="SIMULATED",
            source_provenance_digest=DIGEST_B,
        )
        first = await repository.reserve_operation(
            quest_ref=QUEST_ONE,
            correlation_ref=CORRELATION_ONE,
            operation_type="APPROVE_TRANSCRIPT",
            attempt_version=4,
            request_digest=DIGEST_A,
        )
        second = await repository.reserve_operation(
            quest_ref="ref://simulated/quest/postgres-two",
            correlation_ref="ref://simulated/correlation/postgres-two",
            operation_type="APPROVE_TRANSCRIPT",
            attempt_version=1,
            request_digest=DIGEST_B,
        )
        duplicate = await repository.reserve_operation(
            quest_ref=QUEST_ONE,
            correlation_ref=CORRELATION_ONE,
            operation_type="APPROVE_TRANSCRIPT",
            attempt_version=4,
            request_digest=DIGEST_A,
        )

        assert first.disposition == "reserved"
        assert second.disposition == "reserved"
        assert second.attempt_id != first.attempt_id
        assert duplicate.attempt_id == first.attempt_id
        assert duplicate.disposition == "duplicate_suppressed"
        with pytest.raises(SqlBecomeQuestError, match="mutated_operation_replay"):
            await repository.reserve_operation(
                quest_ref=QUEST_ONE,
                correlation_ref=CORRELATION_ONE,
                operation_type="APPROVE_TRANSCRIPT",
                attempt_version=4,
                request_digest=DIGEST_B,
            )

        async with sessions() as session:
            attempts = (
                await session.scalars(select(BecomeQuestOperationAttemptModel))
            ).all()
        assert len(attempts) == 2


@pytest.mark.anyio
async def test_simulated_status_is_content_free_and_default_off() -> None:
    async with simulated_postgres_sessions() as sessions:
        await seed_simulated_workspace(sessions)
        async with sessions() as session:
            status = await become_quest_status(
                session,
                workspace_key="become-simulated",
            )

        assert status == {
            "quest_projection_enabled": False,
            "quest_activation_max_operations": 0,
            "quest_role_gate_enabled": False,
            "quest_approval_gate_enabled": False,
            "quest_count": 0,
            "quest_participant_count": 0,
            "quest_approval_count": 0,
            "quest_enrollment_count": 0,
            "quest_attempt_count": 0,
            "quest_reconciliation_required": False,
            "quest_external_effects": 0,
        }


def test_simulated_migration_is_single_head_and_does_not_mutate_i4_receipts() -> None:
    migration = Path("alembic/versions/0082_become_quest.py").read_text()

    assert 'revision: str = "0082_become_quest"' in migration
    assert 'down_revision: str | None = "0081_become_i4_recovery_v2"' in migration
    assert "0082_become_free_result" not in migration
    assert "UPDATE become_i4" not in migration
    assert "DELETE FROM become_i4" not in migration
    assert 'drop_table("become_i4' not in migration
    assert "become_i4_recovery_receipts" not in migration
