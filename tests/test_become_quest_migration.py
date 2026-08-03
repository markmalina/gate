"""Non-skipped populated 0081 -> 0082 -> 0083 migration preservation proof."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.fail("DATABASE_URL is mandatory; migration tests may not skip")
    return value


def _upgrade(revision: str) -> None:
    subprocess.run(
        ["alembic", "upgrade", revision],
        check=True,
        env={**os.environ, "DATABASE_URL": _database_url()},
    )


async def _seed_historical_recovery() -> tuple[str, ...]:
    engine = create_async_engine(_database_url())
    recovery_id = uuid4()
    payload = json.dumps(
        {
            "schema": "gate.become-i4-recovery-receipt@1",
            "state": "prepared",
            "enabled": False,
            "external_effects": 0,
        },
        sort_keys=True,
    )
    async with engine.begin() as connection:
        await connection.execute(text("SET session_replication_role = replica"))
        await connection.execute(
            text(
                """
                INSERT INTO become_i4_recovery_receipts (
                    id, workspace_id, correlation_id, failed_slot_id, schema_name,
                    stage, attempt_version, state, enabled,
                    original_request_digest, original_receipt_digest,
                    original_source_sha, target_source_sha, receipt_digest,
                    receipt_payload, prepared_at
                ) VALUES (
                    :id, :workspace_id, :correlation_id, :failed_slot_id,
                    'gate.become-i4-recovery-receipt@1', 'mayaki', 2, 'prepared',
                    false, :digest_a, :digest_b, :source_a, :source_b, :digest_c,
                    CAST(:payload AS jsonb), now()
                )
                """
            ),
            {
                "id": recovery_id,
                "workspace_id": uuid4(),
                "correlation_id": uuid4(),
                "failed_slot_id": uuid4(),
                "digest_a": "a" * 64,
                "digest_b": "b" * 64,
                "digest_c": "c" * 64,
                "source_a": "1" * 40,
                "source_b": "2" * 40,
                "payload": payload,
            },
        )
        await connection.execute(text("SET session_replication_role = origin"))
        row = (
            await connection.execute(
                text(
                    """
                    SELECT id::text, schema_name, stage, attempt_version::text,
                           state, enabled::text, original_request_digest,
                           original_receipt_digest, receipt_digest,
                           receipt_payload::text
                    FROM become_i4_recovery_receipts WHERE id = :id
                    """
                ),
                {"id": recovery_id},
            )
        ).one()
    await engine.dispose()
    return tuple(str(value) for value in row)


async def _seed_0082_quest_graph() -> dict[str, str]:
    engine = create_async_engine(_database_url())
    workspace_id = uuid4()
    quest_id = uuid4()
    expert_id = uuid4()
    learner_id = uuid4()
    enrollment_id = uuid4()
    attempt_id = uuid4()
    expert_ref = f"ref://simulated/person/expert-{expert_id}"
    learner_ref = f"ref://simulated/person/learner-{learner_id}"
    correlation_ref = f"ref://simulated/correlation/{quest_id}"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO workspaces (id, key, display_name, is_active)
                VALUES (:id, :key, 'Migration fixture', true)
                """
            ),
            {"id": workspace_id, "key": f"migration-{workspace_id}"},
        )
        await connection.execute(
            text(
                """
                INSERT INTO become_quests (
                    id, workspace_id, quest_ref, correlation_ref, contract_version,
                    state, expert_person_ref, learner_person_ref, mode,
                    source_provenance_digest
                ) VALUES (
                    :id, :workspace_id, :quest_ref, :correlation_ref,
                    'become.role-correct-quest@1', 'READY_FOR_EXERCISE',
                    :expert_ref, :learner_ref,
                    'SIMULATED', :digest
                )
                """
            ),
            {
                "id": quest_id,
                "workspace_id": workspace_id,
                "quest_ref": f"ref://simulated/quest/{quest_id}",
                "correlation_ref": correlation_ref,
                "expert_ref": expert_ref,
                "learner_ref": learner_ref,
                "digest": "d" * 64,
            },
        )
        for participant_id, role, person_ref, digest in (
            (expert_id, "EXPERT", expert_ref, "e" * 64),
            (learner_id, "LEARNER", learner_ref, "f" * 64),
        ):
            await connection.execute(
                text(
                    """
                    INSERT INTO become_quest_participants (
                        id, quest_id, role, person_ref, provenance_digest, mode, active
                    ) VALUES (
                        :id, :quest_id, :role, :person_ref, :digest,
                        'SIMULATED', true
                    )
                    """
                ),
                {
                    "id": participant_id,
                    "quest_id": quest_id,
                    "role": role,
                    "person_ref": person_ref,
                    "digest": digest,
                },
            )
        for index, artifact_type in enumerate(
            ("TRANSCRIPT", "EXPERT_PROFILE", "PRODUCT", "PROGRAM"),
            start=1,
        ):
            artifact_id = uuid4()
            artifact_digest = f"{index:x}" * 64
            await connection.execute(
                text(
                    """
                    INSERT INTO become_quest_artifacts (
                        id, quest_id, artifact_type, artifact_version,
                        artifact_digest, provider_settled, transcript_available,
                        expert_reviewed, expert_approved, provenance_digest
                    ) VALUES (
                        :id, :quest_id, :artifact_type, 1, :artifact_digest,
                        :provider_settled, :transcript_available,
                        true, false, :provenance_digest
                    )
                    """
                ),
                {
                    "id": artifact_id,
                    "quest_id": quest_id,
                    "artifact_type": artifact_type,
                    "artifact_digest": artifact_digest,
                    "provider_settled": artifact_type == "TRANSCRIPT",
                    "transcript_available": artifact_type == "TRANSCRIPT",
                    "provenance_digest": "a" * 64,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO become_quest_approvals (
                        id, quest_id, artifact_id, approver_person_ref,
                        approver_role, approval_state, approved_at,
                        contract_version, receipt_digest, receipt_payload
                    ) VALUES (
                        :id, :quest_id, :artifact_id, :expert_ref,
                        'EXPERT', 'APPROVED', now(),
                        'become.role-correct-quest@1', :receipt_digest,
                        CAST(:receipt_payload AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "quest_id": quest_id,
                    "artifact_id": artifact_id,
                    "expert_ref": expert_ref,
                    "receipt_digest": f"{index + 4:x}" * 64,
                    "receipt_payload": json.dumps({"external_effects": 0}),
                },
            )
        await connection.execute(
            text(
                """
                INSERT INTO become_quest_enrollments (
                    id, quest_id, learner_person_ref, state,
                    invitation_digest, acceptance_digest, invited_at, accepted_at
                ) VALUES (
                    :id, :quest_id, :learner_ref, 'ENROLLED',
                    :invitation_digest, :acceptance_digest, now(), now()
                )
                """
            ),
            {
                "id": enrollment_id,
                "quest_id": quest_id,
                "learner_ref": learner_ref,
                "invitation_digest": "8" * 64,
                "acceptance_digest": "9" * 64,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO become_quest_operation_attempts (
                    id, quest_id, correlation_ref, operation_type,
                    attempt_version, state, request_digest, receipt_digest,
                    receipt_payload, finished_at
                ) VALUES (
                    :id, :quest_id, :correlation_ref, 'CREATE_QUEST',
                    1, 'COMPLETED', :request_digest, :receipt_digest,
                    CAST(:receipt_payload AS jsonb), now()
                )
                """
            ),
            {
                "id": attempt_id,
                "quest_id": quest_id,
                "correlation_ref": correlation_ref,
                "request_digest": "b" * 64,
                "receipt_digest": "c" * 64,
                "receipt_payload": json.dumps({"external_effects": 0}),
            },
        )
    await engine.dispose()
    return {
        "quest_id": str(quest_id),
        "workspace_id": str(workspace_id),
        "quest_ref": f"ref://simulated/quest/{quest_id}",
        "correlation_ref": correlation_ref,
        "expert_ref": expert_ref,
        "learner_ref": learner_ref,
        "enrollment_id": str(enrollment_id),
        "attempt_id": str(attempt_id),
    }


async def _read_row(table: str, row_id: str, columns: str) -> tuple[str, ...]:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(f"SELECT {columns} FROM {table} WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            )
        ).one()
    await engine.dispose()
    return tuple(str(value) for value in row)


def test_populated_historical_schema_upgrade_preserves_recovery_and_quest_rows() -> None:
    _upgrade("0081_become_i4_recovery_v2")
    recovery_before = asyncio.run(_seed_historical_recovery())
    _upgrade("0082_become_quest")
    graph = asyncio.run(_seed_0082_quest_graph())
    quest_before = asyncio.run(
        _read_row(
            "become_quests",
            graph["quest_id"],
            "id::text, workspace_id::text, quest_ref, correlation_ref, "
            "contract_version, state, expert_person_ref, learner_person_ref, "
            "mode, source_provenance_digest",
        )
    )
    _upgrade("0083_become_quest_corrective")

    recovery_after = asyncio.run(
        _read_row(
            "become_i4_recovery_receipts",
            recovery_before[0],
            "id::text, schema_name, stage, attempt_version::text, state, "
            "enabled::text, original_request_digest, original_receipt_digest, "
            "receipt_digest, receipt_payload::text",
        )
    )
    quest_after = asyncio.run(
        _read_row(
            "become_quests",
            graph["quest_id"],
            "id::text, workspace_id::text, quest_ref, correlation_ref, "
            "contract_version, state, expert_person_ref, learner_person_ref, "
            "mode, source_provenance_digest",
        )
    )
    async def read_corrective_graph() -> tuple[tuple[object, ...], ...]:
        engine = create_async_engine(_database_url())
        async with engine.connect() as connection:
            counts = (
                await connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM become_quest_participants
                           WHERE quest_id = :quest_id),
                          (SELECT count(*) FROM become_quest_artifacts
                           WHERE quest_id = :quest_id),
                          (SELECT count(*) FROM become_quest_approvals
                           WHERE quest_id = :quest_id),
                          (SELECT count(*) FROM become_quest_approvals a
                           JOIN become_quest_participants p
                             ON p.quest_id = a.quest_id
                            AND p.role = a.approver_role
                            AND p.person_ref = a.approver_person_ref
                            AND p.active = a.approver_active
                           WHERE a.quest_id = :quest_id
                             AND a.approver_role = 'EXPERT'
                             AND a.approver_active = true)
                        """
                    ),
                    {"quest_id": graph["quest_id"]},
                )
            ).one()
            enrollment = (
                await connection.execute(
                    text(
                        """
                        SELECT state, learner_person_ref, learner_role,
                               learner_active, invitation_ref,
                               invitation_version, invitation_digest,
                               acceptance_digest, accepted_at IS NOT NULL
                        FROM become_quest_enrollments WHERE id = :id
                        """
                    ),
                    {"id": graph["enrollment_id"]},
                )
            ).one()
            attempt = (
                await connection.execute(
                    text(
                        """
                        SELECT correlation_ref, operation_type, attempt_version,
                               idempotency_key, state, request_digest,
                               receipt_digest
                        FROM become_quest_operation_attempts WHERE id = :id
                        """
                    ),
                    {"id": graph["attempt_id"]},
                )
            ).one()
        result = tuple(counts), tuple(enrollment), tuple(attempt)
        await engine.dispose()
        return result

    counts, enrollment, attempt = asyncio.run(read_corrective_graph())
    assert recovery_after == recovery_before
    assert quest_after == quest_before
    assert counts == (2, 4, 4, 4)
    assert enrollment == (
        "ENROLLED",
        graph["learner_ref"],
        "LEARNER",
        True,
        f"ref://migration/invitation/{graph['enrollment_id']}",
        1,
        "8" * 64,
        "9" * 64,
        True,
    )
    assert attempt == (
        graph["correlation_ref"],
        "CREATE_QUEST",
        1,
        f"migrated:{graph['attempt_id']}",
        "COMPLETED",
        "b" * 64,
        "c" * 64,
    )


def test_migration_graph_is_linear_and_0082_is_not_rewritten() -> None:
    original = Path("alembic/versions/0082_become_quest.py").read_text()
    corrective = Path(
        "alembic/versions/0083_become_quest_corrective.py"
    ).read_text()
    assert 'revision: str = "0082_become_quest"' in original
    assert 'down_revision: str | None = "0081_become_i4_recovery_v2"' in original
    assert 'revision: str = "0083_become_quest_corrective"' in corrective
    assert 'down_revision: str | None = "0082_become_quest"' in corrective
    assert "UPDATE become_i4" not in corrective
    assert "DELETE FROM become_i4" not in corrective
    assert "not a safe production rollback" in corrective
    heads = subprocess.run(
        ["alembic", "heads"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": _database_url()},
    ).stdout.strip().splitlines()
    assert len(heads) == 1
    assert heads[0].startswith("0083_become_quest_corrective")
