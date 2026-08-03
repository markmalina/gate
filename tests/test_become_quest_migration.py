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


async def _seed_0082_quest() -> tuple[str, ...]:
    engine = create_async_engine(_database_url())
    workspace_id = uuid4()
    quest_id = uuid4()
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
                    'become.role-correct-quest@1', 'DRAFT', NULL, NULL,
                    'SIMULATED', :digest
                )
                """
            ),
            {
                "id": quest_id,
                "workspace_id": workspace_id,
                "quest_ref": f"ref://simulated/quest/{quest_id}",
                "correlation_ref": f"ref://simulated/correlation/{quest_id}",
                "digest": "d" * 64,
            },
        )
        row = (
            await connection.execute(
                text(
                    """
                    SELECT id::text, workspace_id::text, quest_ref, correlation_ref,
                           contract_version, state, mode, source_provenance_digest
                    FROM become_quests WHERE id = :id
                    """
                ),
                {"id": quest_id},
            )
        ).one()
    await engine.dispose()
    return tuple(str(value) for value in row)


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
    quest_before = asyncio.run(_seed_0082_quest())
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
            quest_before[0],
            "id::text, workspace_id::text, quest_ref, correlation_ref, "
            "contract_version, state, mode, source_provenance_digest",
        )
    )
    assert recovery_after == recovery_before
    assert quest_after == quest_before


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
