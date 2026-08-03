"""SIMULATED non-skipped PostgreSQL proofs for the production Quest coordinator."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.application.become_quest import ArtifactType, canonical_digest
from app.infrastructure.become_quest import (
    PostgresBecomeQuestCoordinator,
    SqlBecomeQuestError,
    SqlBecomeQuestRepository,
    become_quest_status,
)
from app.models.become_quest import (
    BecomeQuestApprovalModel,
    BecomeQuestArtifactModel,
    BecomeQuestEnrollmentModel,
    BecomeQuestModel,
    BecomeQuestOperationAttemptModel,
    BecomeQuestParticipantModel,
)
from app.models.canonical import Workspace
from app.models.domain import Base
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
QUEST_ONE = "ref://simulated/quest/postgres-one"
QUEST_TWO = "ref://simulated/quest/postgres-two"
CORRELATION = "ref://simulated/correlation/shared"
EXPERT = "ref://simulated/person/lesha"
LEARNER = "ref://simulated/person/mark"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def postgres_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL is mandatory; PostgreSQL Quest tests may not skip")
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


async def seed_workspace(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        session.add(
            Workspace(
                key="become-simulated",
                display_name="BECOME SIMULATED",
                is_active=True,
            )
        )


def command(
    operation: str,
    key: str,
    *,
    quest_id: str = QUEST_ONE,
    correlation_id: str = CORRELATION,
    **fields: object,
) -> dict[str, object]:
    return {
        "quest_id": quest_id,
        "correlation_id": correlation_id,
        "operation": operation,
        "attempt_version": 1,
        "idempotency_key": key,
        "occurred_at": "2026-08-03T09:00:00+00:00",
        **fields,
    }


async def create_and_bind(
    coordinator: PostgresBecomeQuestCoordinator,
    *,
    quest_id: str = QUEST_ONE,
    correlation_id: str = CORRELATION,
    mode: str = "SIMULATED",
) -> None:
    await coordinator.execute(
        command(
            "CREATE_QUEST",
            "create",
            quest_id=quest_id,
            correlation_id=correlation_id,
            mode=mode,
            source_provenance_digest="1" * 64,
        )
    )
    await coordinator.execute(
        command(
            "BIND_PARTICIPANT",
            "bind-expert",
            quest_id=quest_id,
            correlation_id=correlation_id,
            role="EXPERT",
            person_ref=EXPERT,
            provenance_digest="2" * 64,
        )
    )
    await coordinator.execute(
        command(
            "BIND_PARTICIPANT",
            "bind-learner",
            quest_id=quest_id,
            correlation_id=correlation_id,
            role="LEARNER",
            person_ref=LEARNER,
            provenance_digest="3" * 64,
        )
    )


async def approve(
    coordinator: PostgresBecomeQuestCoordinator,
    artifact_type: ArtifactType,
    artifact_digest: str,
    *,
    key: str,
    version: int = 1,
) -> dict[str, object]:
    return await coordinator.execute(
        command(
            "APPROVE_ARTIFACT",
            key,
            artifact_type=artifact_type.value,
            artifact_version=version,
            artifact_digest=artifact_digest,
            approver_person_ref=EXPERT,
            approver_role="EXPERT",
        )
    )


async def full_human_path(
    coordinator: PostgresBecomeQuestCoordinator,
) -> dict[str, object]:
    await create_and_bind(coordinator)
    await coordinator.execute(
        command(
            "EXPERT_CONSENT",
            "consent",
            person_ref=EXPERT,
            consent_ref="ref://simulated/consent/expert",
        )
    )
    await coordinator.execute(
        command(
            "PROVIDER_TRANSCRIPT_SETTLED",
            "transcript-v1",
            artifact_version=1,
            artifact_digest=DIGEST_A,
            provenance_digest="4" * 64,
        )
    )
    await approve(
        coordinator,
        ArtifactType.TRANSCRIPT,
        DIGEST_A,
        key="approve-transcript",
    )
    for index, artifact_type in enumerate(
        (
            ArtifactType.EXPERT_PROFILE,
            ArtifactType.PRODUCT,
            ArtifactType.PROGRAM,
        ),
        start=2,
    ):
        artifact_digest = f"{index:x}" * 64
        await coordinator.execute(
            command(
                "REGISTER_ARTIFACT",
                f"register-{artifact_type.value}",
                artifact_type=artifact_type.value,
                artifact_version=1,
                artifact_digest=artifact_digest,
                provenance_digest="5" * 64,
            )
        )
        await approve(
            coordinator,
            artifact_type,
            artifact_digest,
            key=f"approve-{artifact_type.value}",
        )
    await coordinator.execute(
        command(
            "INVITE_LEARNER",
            "invite-v1",
            learner_person_ref=LEARNER,
            invitation_ref="ref://simulated/invitation/learner-v1",
            invitation_version=1,
            invitation_digest="f" * 64,
        )
    )
    return await coordinator.execute(
        command(
            "ACCEPT_LEARNER",
            "accept-v1",
            learner_person_ref=LEARNER,
            invitation_ref="ref://simulated/invitation/learner-v1",
            invitation_version=1,
            invitation_digest="f" * 64,
            acceptance_ref="ref://simulated/acceptance/learner-v1",
            acceptance_digest="9" * 64,
        )
    )


@pytest.mark.anyio
async def test_postgres_restart_restores_full_human_state_and_exact_replay() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        first_process = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        terminal = await full_human_path(first_process)

        restarted = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        replay = await restarted.execute(
            command(
                "ACCEPT_LEARNER",
                "accept-v1",
                learner_person_ref=LEARNER,
                invitation_ref="ref://simulated/invitation/learner-v1",
                invitation_version=1,
                invitation_digest="f" * 64,
                acceptance_ref="ref://simulated/acceptance/learner-v1",
                acceptance_digest="9" * 64,
            )
        )
        assert replay == terminal
        assert terminal["state"] == "READY_FOR_EXERCISE"
        assert terminal["external_effects"] == 0

        async with sessions() as session:
            quest = await session.scalar(
                select(BecomeQuestModel).where(BecomeQuestModel.quest_ref == QUEST_ONE)
            )
            artifact_count = await session.scalar(
                select(func.count()).select_from(BecomeQuestArtifactModel)
            )
            approval_count = await session.scalar(
                select(func.count()).select_from(BecomeQuestApprovalModel)
            )
            enrollment = await session.scalar(select(BecomeQuestEnrollmentModel))
            attempt_count = await session.scalar(
                select(func.count()).select_from(BecomeQuestOperationAttemptModel)
            )
        assert quest is not None and quest.state == "READY_FOR_EXERCISE"
        assert artifact_count == 4
        assert approval_count == 4
        assert enrollment is not None and enrollment.state == "ENROLLED"
        assert enrollment.acceptance_digest == "9" * 64
        assert attempt_count == 14


@pytest.mark.anyio
async def test_postgres_executes_shared_create_quest_parity_fixture() -> None:
    matrix = json.loads(
        Path("contracts/quest/v1/operation-contract-matrix.json").read_text()
    )
    fixture = matrix["executable_fixtures"]["create_quest"]
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        receipt = await coordinator.execute(fixture["command"])
        assert receipt == fixture["expected_receipt"]
        for field in matrix["receipt_required"]:
            assert field in receipt
        assert await coordinator.execute(fixture["command"]) == receipt
        with pytest.raises(SqlBecomeQuestError, match="mutated_replay"):
            await coordinator.execute({**fixture["command"], "mode": "PRODUCTION"})
        with pytest.raises(SqlBecomeQuestError, match="operation_unsupported"):
            await coordinator.execute(
                {
                    **fixture["command"],
                    "operation": "UNSUPPORTED",
                    "idempotency_key": "unsupported-v1",
                }
            )


@pytest.mark.anyio
async def test_postgres_transcript_version_history_invalidates_current_approval() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        await create_and_bind(coordinator)
        await coordinator.execute(
            command(
                "EXPERT_CONSENT",
                "consent",
                person_ref=EXPERT,
                consent_ref="ref://simulated/consent/expert",
            )
        )
        await coordinator.execute(
            command(
                "PROVIDER_TRANSCRIPT_SETTLED",
                "transcript-v1",
                artifact_version=1,
                artifact_digest=DIGEST_A,
                provenance_digest="4" * 64,
            )
        )
        await approve(
            coordinator,
            ArtifactType.TRANSCRIPT,
            DIGEST_A,
            key="approve-transcript-v1",
        )
        await coordinator.execute(
            command(
                "PROVIDER_TRANSCRIPT_SETTLED",
                "transcript-v2",
                artifact_version=2,
                artifact_digest=DIGEST_B,
                provenance_digest="5" * 64,
            )
        )
        async with sessions() as session:
            quest = await session.scalar(select(BecomeQuestModel))
            artifacts = (
                await session.scalars(
                    select(BecomeQuestArtifactModel).order_by(
                        BecomeQuestArtifactModel.artifact_version
                    )
                )
            ).all()
            approvals = (
                await session.scalars(select(BecomeQuestApprovalModel))
            ).all()
        assert quest is not None and quest.state == "TRANSCRIPT_PENDING_REVIEW"
        assert [artifact.artifact_version for artifact in artifacts] == [1, 2]
        assert len(approvals) == 1
        assert approvals[0].artifact_id == artifacts[0].id


@pytest.mark.anyio
async def test_postgres_profile_program_versions_recompute_readiness_after_restart() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        await full_human_path(coordinator)

        for expected_approval_count, artifact_type, digest, key in (
            (4, ArtifactType.EXPERT_PROFILE, "6" * 64, "profile-v2"),
            (5, ArtifactType.PROGRAM, "7" * 64, "program-v2"),
        ):
            await coordinator.execute(
                command(
                    "REGISTER_ARTIFACT",
                    key,
                    artifact_type=artifact_type.value,
                    artifact_version=2,
                    artifact_digest=digest,
                    provenance_digest="8" * 64,
                )
            )
            restarted = PostgresBecomeQuestCoordinator(
                sessions, workspace_key="become-simulated"
            )
            async with sessions() as session:
                quest = await session.scalar(select(BecomeQuestModel))
                approval_count = await session.scalar(
                    select(func.count()).select_from(BecomeQuestApprovalModel)
                )
            assert quest is not None and quest.state != "READY_FOR_EXERCISE"
            assert approval_count == expected_approval_count
            await approve(
                restarted,
                artifact_type,
                digest,
                key=f"approve-{key}",
                version=2,
            )
            async with sessions() as session:
                restored = await session.scalar(select(BecomeQuestModel))
            assert restored is not None and restored.state == "READY_FOR_EXERCISE"
            coordinator = restarted

        await coordinator.execute(
            command(
                "REGISTER_ARTIFACT",
                "exercise-v1",
                artifact_type=ArtifactType.EXERCISE.value,
                artifact_version=1,
                artifact_digest="e" * 64,
                provenance_digest="8" * 64,
            )
        )
        async with sessions() as session:
            exercise = await session.scalar(select(BecomeQuestModel))
        assert exercise is not None and exercise.state == "READY_FOR_EXERCISE"


@pytest.mark.anyio
async def test_postgres_exact_binding_is_noop_after_ready() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        await full_human_path(coordinator)
        for role, person_ref, digest in (
            ("EXPERT", EXPERT, "2" * 64),
            ("LEARNER", LEARNER, "3" * 64),
        ):
            receipt = await coordinator.execute(
                command(
                    "BIND_PARTICIPANT",
                    f"rebind-{role}",
                    role=role,
                    person_ref=person_ref,
                    provenance_digest=digest,
                )
            )
            assert receipt["state"] == "READY_FOR_EXERCISE"
        with pytest.raises(SqlBecomeQuestError, match="role_rebinding_forbidden"):
            await coordinator.execute(
                command(
                    "BIND_PARTICIPANT",
                    "rebind-changed",
                    role="EXPERT",
                    person_ref="ref://simulated/person/other",
                    provenance_digest="2" * 64,
                )
            )


@pytest.mark.anyio
async def test_postgres_same_idempotency_and_correlation_are_safe_across_quests() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        first = await coordinator.execute(
            command(
                "CREATE_QUEST",
                "shared-key",
                quest_id=QUEST_ONE,
                mode="SIMULATED",
                source_provenance_digest=DIGEST_A,
            )
        )
        second = await coordinator.execute(
            command(
                "CREATE_QUEST",
                "shared-key",
                quest_id=QUEST_TWO,
                mode="SIMULATED",
                source_provenance_digest=DIGEST_B,
            )
        )
        assert first["quest_id"] != second["quest_id"]
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(BecomeQuestModel)
                )
                == 2
            )


@pytest.mark.anyio
async def test_postgres_exact_and_mutated_replay_survive_restart() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        first = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        await first.execute(
            command(
                "CREATE_QUEST",
                "create",
                mode="SIMULATED",
                source_provenance_digest=DIGEST_A,
            )
        )
        request = command(
            "BIND_PARTICIPANT",
            "bind-expert",
            role="EXPERT",
            person_ref=EXPERT,
            provenance_digest=DIGEST_A,
        )
        receipt = await first.execute(request)
        restarted = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        assert await restarted.execute(request) == receipt
        with pytest.raises(SqlBecomeQuestError, match="mutated_replay"):
            await restarted.execute({**request, "provenance_digest": DIGEST_B})


@pytest.mark.anyio
async def test_postgres_reserved_crash_requires_reconciliation_and_no_retry() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        await coordinator.execute(
            command(
                "CREATE_QUEST",
                "create",
                mode="SIMULATED",
                source_provenance_digest=DIGEST_A,
            )
        )
        request = command(
            "BIND_PARTICIPANT",
            "crash-after-reserved",
            role="EXPERT",
            person_ref=EXPERT,
            provenance_digest=DIGEST_A,
        )
        repository = SqlBecomeQuestRepository(sessions)
        reservation = await repository.reserve_operation(
            quest_ref=QUEST_ONE,
            correlation_ref=CORRELATION,
            operation_type="BIND_PARTICIPANT",
            attempt_version=1,
            idempotency_key="crash-after-reserved",
            request_digest=canonical_digest(request),
        )
        assert reservation.disposition == "reserved"
        restarted = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        with pytest.raises(
            SqlBecomeQuestError, match="operation_reconciliation_required"
        ):
            await restarted.execute(request)
        async with sessions() as session:
            status = await become_quest_status(
                session, workspace_key="become-simulated"
            )
            participants = await session.scalar(
                select(func.count()).select_from(BecomeQuestParticipantModel)
            )
        assert status["quest_reconciliation_required"] is True
        assert participants == 0


@pytest.mark.anyio
async def test_postgres_concurrent_create_bind_and_reserve_are_singleton() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        first = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        second = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        create = command(
            "CREATE_QUEST",
            "create",
            mode="SIMULATED",
            source_provenance_digest=DIGEST_A,
        )
        created = await asyncio.gather(first.execute(create), second.execute(create))
        assert created[0] == created[1]
        bind = command(
            "BIND_PARTICIPANT",
            "bind-expert",
            role="EXPERT",
            person_ref=EXPERT,
            provenance_digest=DIGEST_A,
        )
        bound = await asyncio.gather(first.execute(bind), second.execute(bind))
        assert bound[0] == bound[1]

        repository = SqlBecomeQuestRepository(sessions)
        reservations = await asyncio.gather(
            *(
                repository.reserve_operation(
                    quest_ref=QUEST_ONE,
                    correlation_ref=CORRELATION,
                    operation_type="NO_EXTERNAL_EFFECT",
                    attempt_version=1,
                    idempotency_key="reserve-shared",
                    request_digest=DIGEST_B,
                )
                for _ in range(2)
            )
        )
        assert reservations[0].attempt_id == reservations[1].attempt_id
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(BecomeQuestParticipantModel)
                )
                == 1
            )


@pytest.mark.anyio
async def test_postgres_constraints_reject_cross_quest_approval_and_bad_enrollment() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        await create_and_bind(coordinator)
        await coordinator.execute(
            command(
                "CREATE_QUEST",
                "create-two",
                quest_id=QUEST_TWO,
                correlation_id="ref://simulated/correlation/two",
                mode="SIMULATED",
                source_provenance_digest=DIGEST_B,
            )
        )
        with pytest.raises(IntegrityError):
            async with sessions() as session, session.begin():
                first_quest = await session.scalar(
                    select(BecomeQuestModel).where(
                        BecomeQuestModel.quest_ref == QUEST_ONE
                    )
                )
                second_quest = await session.scalar(
                    select(BecomeQuestModel).where(
                        BecomeQuestModel.quest_ref == QUEST_TWO
                    )
                )
                assert first_quest is not None and second_quest is not None
                artifact = BecomeQuestArtifactModel(
                    quest_id=first_quest.id,
                    artifact_type="TRANSCRIPT",
                    artifact_version=1,
                    artifact_digest=DIGEST_A,
                    provider_settled=True,
                    transcript_available=True,
                    expert_reviewed=False,
                    expert_approved=False,
                    provenance_digest=DIGEST_A,
                )
                session.add(artifact)
                await session.flush()
                session.add(
                    BecomeQuestApprovalModel(
                        quest_id=second_quest.id,
                        artifact_id=artifact.id,
                        approver_person_ref=EXPERT,
                        approver_role="EXPERT",
                        approver_active=True,
                        approval_state="APPROVED",
                        approved_at=datetime.fromisoformat(
                            "2026-08-03T09:00:00+00:00"
                        ),
                        contract_version="become.role-correct-quest@1",
                        receipt_digest=DIGEST_B,
                        receipt_payload={"external_effects": 0},
                    )
                )
                await session.flush()


@pytest.mark.anyio
async def test_postgres_approval_requires_exact_bound_quest_expert() -> None:
    other_expert = "ref://simulated/person/expert-other-quest"
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        await create_and_bind(coordinator)
        await coordinator.execute(
            command(
                "EXPERT_CONSENT",
                "consent",
                person_ref=EXPERT,
                consent_ref="ref://simulated/consent/expert",
            )
        )
        await coordinator.execute(
            command(
                "PROVIDER_TRANSCRIPT_SETTLED",
                "transcript-v1",
                artifact_version=1,
                artifact_digest=DIGEST_A,
                provenance_digest="4" * 64,
            )
        )
        await coordinator.execute(
            command(
                "CREATE_QUEST",
                "create-other",
                quest_id=QUEST_TWO,
                correlation_id="ref://simulated/correlation/other",
                mode="SIMULATED",
                source_provenance_digest=DIGEST_B,
            )
        )
        await coordinator.execute(
            command(
                "BIND_PARTICIPANT",
                "bind-other-expert",
                quest_id=QUEST_TWO,
                correlation_id="ref://simulated/correlation/other",
                role="EXPERT",
                person_ref=other_expert,
                provenance_digest=DIGEST_B,
            )
        )
        async with sessions() as session:
            quest = await session.scalar(
                select(BecomeQuestModel).where(BecomeQuestModel.quest_ref == QUEST_ONE)
            )
            assert quest is not None
            artifact = await session.scalar(
                select(BecomeQuestArtifactModel).where(
                    BecomeQuestArtifactModel.quest_id == quest.id
                )
            )
        assert artifact is not None

        async def insert_approval(person_ref: str) -> None:
            async with sessions() as session, session.begin():
                session.add(
                    BecomeQuestApprovalModel(
                        quest_id=quest.id,
                        artifact_id=artifact.id,
                        approver_person_ref=person_ref,
                        approver_role="EXPERT",
                        approver_active=True,
                        approval_state="APPROVED",
                        approved_at=datetime.fromisoformat(
                            "2026-08-03T09:00:00+00:00"
                        ),
                        contract_version="become.role-correct-quest@1",
                        receipt_digest=DIGEST_B,
                        receipt_payload={"external_effects": 0},
                    )
                )
                await session.flush()

        for invalid_person in (
            "ref://simulated/person/arbitrary",
            LEARNER,
            other_expert,
        ):
            with pytest.raises(IntegrityError):
                await insert_approval(invalid_person)

        await insert_approval(EXPERT)
        async with sessions() as session:
            approvals = (
                await session.scalars(select(BecomeQuestApprovalModel))
            ).all()
        assert len(approvals) == 1
        assert approvals[0].approver_person_ref == EXPERT

        with pytest.raises(IntegrityError):
            async with sessions() as session, session.begin():
                quest = await session.scalar(
                    select(BecomeQuestModel).where(
                        BecomeQuestModel.quest_ref == QUEST_ONE
                    )
                )
                assert quest is not None
                session.add(
                    BecomeQuestEnrollmentModel(
                        quest_id=quest.id,
                        learner_person_ref=LEARNER,
                        learner_role="LEARNER",
                        learner_active=True,
                        state="ENROLLED",
                        invitation_ref="ref://simulated/invitation/invalid",
                        invitation_version=1,
                        invitation_digest=DIGEST_A,
                        acceptance_digest=None,
                        invited_at=datetime.fromisoformat(
                            "2026-08-03T09:00:00+00:00"
                        ),
                        accepted_at=None,
                    )
                )
                await session.flush()


@pytest.mark.anyio
async def test_postgres_constraints_reject_mode_mismatch_and_synthetic_sql() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        coordinator = PostgresBecomeQuestCoordinator(
            sessions, workspace_key="become-simulated"
        )
        await coordinator.execute(
            command(
                "CREATE_QUEST",
                "create-production",
                mode="PRODUCTION",
                source_provenance_digest=DIGEST_A,
            )
        )
        async with sessions() as session:
            quest = await session.scalar(select(BecomeQuestModel))
        assert quest is not None

        for person_ref, mode in (
            ("ref://person/non-synthetic", "SIMULATED"),
            ("ref://simulated/person/direct-sql", "PRODUCTION"),
        ):
            with pytest.raises(IntegrityError):
                async with sessions() as session, session.begin():
                    session.add(
                        BecomeQuestParticipantModel(
                            quest_id=quest.id,
                            role="EXPERT",
                            person_ref=person_ref,
                            provenance_digest=DIGEST_A,
                            mode=mode,
                            active=True,
                        )
                    )
                    await session.flush()


@pytest.mark.anyio
async def test_postgres_status_remains_content_free_and_default_off() -> None:
    async with postgres_sessions() as sessions:
        await seed_workspace(sessions)
        async with sessions() as session:
            status = await become_quest_status(
                session, workspace_key="become-simulated"
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
