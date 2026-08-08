"""Non-skipped populated 0081 -> 0082 -> 0083 migration preservation proof."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@dataclass(frozen=True)
class HistoricalRecoveryGraph:
    recovery_id: str
    workspace_id: str
    media_intake_id: str
    correlation_id: str
    failed_slot_id: str
    row: tuple[str, ...]


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


async def _seed_historical_recovery() -> HistoricalRecoveryGraph:
    engine = create_async_engine(_database_url())
    workspace_id = uuid4()
    connector_id = uuid4()
    dialog_id = uuid4()
    installation_id = uuid4()
    registration_source_id = uuid4()
    consent_source_id = uuid4()
    media_source_id = uuid4()
    registration_receipt_id = uuid4()
    consent_receipt_id = uuid4()
    relations_handoff_id = uuid4()
    media_intake_id = uuid4()
    correlation_id = uuid4()
    failed_slot_id = uuid4()
    recovery_id = uuid4()
    installation_ref = f"ref://migration/installation/{installation_id}"
    subject_ref = f"ref://migration/subject/{workspace_id}"
    correlation_ref = f"synthetic://correlation/voice/{correlation_id.hex}"
    failed_request_digest = "1" * 64
    failed_receipt_digest = "2" * 64
    failed_source_sha = "3" * 40
    recovery_payload = json.dumps(
        {
            "schema": "gate.become-i4-recovery-receipt@1",
            "state": "prepared",
            "enabled": False,
            "external_effects": 0,
        },
        sort_keys=True,
    )
    failed_payload = json.dumps(
        {
            "schema": "gate.become-i4-stage-outcome@1",
            "stage": "mayaki",
            "classification": "failed",
            "error_code": "connection_not_established",
            "external_effects": 0,
        },
        sort_keys=True,
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO workspaces (id, key, display_name, is_active)
                VALUES (:id, :key, 'Historical recovery migration fixture', true)
                """
            ),
            {"id": workspace_id, "key": f"migration-recovery-{workspace_id}"},
        )
        await connection.execute(
            text(
                """
                INSERT INTO transport_connectors (
                    id, workspace_id, connector_key, connector_kind, is_enabled
                ) VALUES (
                    :id, :workspace_id, 'telegram_become_group',
                    'telegram_bot', false
                )
                """
            ),
            {"id": connector_id, "workspace_id": workspace_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO dialogs (
                    id, workspace_id, connector_id, provider_dialog_id, dialog_kind
                ) VALUES (
                    :id, :workspace_id, :connector_id, :provider_dialog_id, 'group'
                )
                """
            ),
            {
                "id": dialog_id,
                "workspace_id": workspace_id,
                "connector_id": connector_id,
                "provider_dialog_id": f"migration-dialog-{dialog_id}",
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO agent_installations (
                    id, workspace_id, connector_id, dialog_id, installation_key,
                    dialog_label, state
                ) VALUES (
                    :id, :workspace_id, :connector_id, :dialog_id,
                    :installation_key, 'Migration fixture', 'discovered'
                )
                """
            ),
            {
                "id": installation_id,
                "workspace_id": workspace_id,
                "connector_id": connector_id,
                "dialog_id": dialog_id,
                "installation_key": f"migration-recovery-{installation_id}",
            },
        )
        for source_id, bot_key in (
            (registration_source_id, "migration-registration"),
            (consent_source_id, "migration-consent"),
            (media_source_id, "migration-media"),
        ):
            await connection.execute(
                text(
                    """
                    INSERT INTO telegram_receipts (
                        id, bot_key, update_id, payload, status, received_at
                    ) VALUES (
                        :id, :bot_key, :update_id, CAST(:payload AS jsonb),
                        'processed', now()
                    )
                    """
                ),
                {
                    "id": source_id,
                    "bot_key": bot_key,
                    "update_id": source_id.int % 9_000_000_000_000_000_000,
                    "payload": json.dumps({"external_effects": 0}),
                },
            )
        await connection.execute(
            text(
                """
                INSERT INTO become_controlled_intake_receipts (
                    id, workspace_id, installation_id, source_receipt_id,
                    receipt_key, receipt_digest, schema_name, connector_key,
                    intent, installation_ref, subject_ref, update_ref,
                    group_state, member_count, payload, received_at
                ) VALUES (
                    :id, :workspace_id, :installation_id, :source_receipt_id,
                    :receipt_key, :receipt_digest,
                    'gate.become-controlled-intake@1', 'telegram_become_group',
                    'registration_confirmed', :installation_ref, :subject_ref,
                    :update_ref, 'owner_only', 2, CAST(:payload AS jsonb), now()
                )
                """
            ),
            {
                "id": registration_receipt_id,
                "workspace_id": workspace_id,
                "installation_id": installation_id,
                "source_receipt_id": registration_source_id,
                "receipt_key": f"migration-registration-{registration_receipt_id}",
                "receipt_digest": "a" * 64,
                "installation_ref": installation_ref,
                "subject_ref": subject_ref,
                "update_ref": f"ref://migration/update/{registration_source_id}",
                "payload": json.dumps({"external_effects": 0}),
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO become_controlled_consent_receipts (
                    id, workspace_id, installation_id, source_receipt_id,
                    receipt_key, receipt_digest, schema_name, connector_key,
                    intent, installation_ref, quest_ref, correlation_ref,
                    subject_ref, action_ref, policy_version, policy_digest,
                    group_state, member_count, payload, accepted_at
                ) VALUES (
                    :id, :workspace_id, :installation_id, :source_receipt_id,
                    :receipt_key, :receipt_digest,
                    'gate.become-controlled-consent-intake@1',
                    'telegram_become_group', 'consent_accepted',
                    :installation_ref, :quest_ref, :correlation_ref,
                    :subject_ref, :action_ref, 'migration-policy-v1',
                    :policy_digest, 'owner_only', 2, CAST(:payload AS jsonb), now()
                )
                """
            ),
            {
                "id": consent_receipt_id,
                "workspace_id": workspace_id,
                "installation_id": installation_id,
                "source_receipt_id": consent_source_id,
                "receipt_key": f"migration-consent-{consent_receipt_id}",
                "receipt_digest": "b" * 64,
                "installation_ref": installation_ref,
                "quest_ref": f"ref://migration/quest/{workspace_id}",
                "correlation_ref": f"ref://migration/consent/{correlation_id}",
                "subject_ref": subject_ref,
                "action_ref": f"ref://migration/action/{consent_receipt_id}",
                "policy_digest": "c" * 64,
                "payload": json.dumps({"external_effects": 0}),
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO become_relations_consent_handoffs (
                    id, workspace_id, consent_receipt_id, handoff_key,
                    command_digest, schema_name, status, payload
                ) VALUES (
                    :id, :workspace_id, :consent_receipt_id, :handoff_key,
                    :command_digest, 'relations.controlled-consent-command@1',
                    'pending_delivery', CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "id": relations_handoff_id,
                "workspace_id": workspace_id,
                "consent_receipt_id": consent_receipt_id,
                "handoff_key": f"migration-handoff-{relations_handoff_id}",
                "command_digest": "d" * 64,
                "payload": json.dumps({"external_effects": 0}),
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO become_media_intakes (
                    id, workspace_id, installation_id, source_receipt_id,
                    registration_receipt_id, consent_receipt_id,
                    relations_handoff_id, intake_key, schema_name, subject_ref,
                    update_ref, kind, file_unique_digest, duration_seconds,
                    status, raw_expires_at, transcript_state,
                    confirmed_transcript, transcript_digest,
                    transcript_confirmed_at, transcript_expires_at, received_at
                ) VALUES (
                    :id, :workspace_id, :installation_id, :source_receipt_id,
                    :registration_receipt_id, :consent_receipt_id,
                    :relations_handoff_id, :intake_key,
                    'gate.become-media-intake@1', :subject_ref, :update_ref,
                    'voice', :file_unique_digest, 1, 'pending_download',
                    now() + interval '7 days', 'confirmed',
                    'historical migration fixture', :transcript_digest,
                    now(), now() + interval '30 days', now()
                )
                """
            ),
            {
                "id": media_intake_id,
                "workspace_id": workspace_id,
                "installation_id": installation_id,
                "source_receipt_id": media_source_id,
                "registration_receipt_id": registration_receipt_id,
                "consent_receipt_id": consent_receipt_id,
                "relations_handoff_id": relations_handoff_id,
                "intake_key": f"migration-media-{media_intake_id}",
                "subject_ref": subject_ref,
                "update_ref": f"ref://migration/update/{media_source_id}",
                "file_unique_digest": "e" * 64,
                "transcript_digest": "f" * 64,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO become_voice_correlations (
                    id, workspace_id, media_intake_id, correlation_ref,
                    schema_name, intake_digest
                ) VALUES (
                    :id, :workspace_id, :media_intake_id, :correlation_ref,
                    'gate.become-voice-correlation@1', :intake_digest
                )
                """
            ),
            {
                "id": correlation_id,
                "workspace_id": workspace_id,
                "media_intake_id": media_intake_id,
                "correlation_ref": correlation_ref,
                "intake_digest": "0" * 64,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO become_voice_chain_slots (
                    id, workspace_id, correlation_id, stage, position,
                    schema_name, state, request_digest, request_payload,
                    receipt_digest, receipt_payload, source_sha, terminal_at
                ) VALUES (
                    :id, :workspace_id, :correlation_id, 'mayaki', 2,
                    'gate.become-chain-slot@1', 'failed', :request_digest,
                    CAST(:request_payload AS jsonb), :receipt_digest,
                    CAST(:receipt_payload AS jsonb), :source_sha, now()
                )
                """
            ),
            {
                "id": failed_slot_id,
                "workspace_id": workspace_id,
                "correlation_id": correlation_id,
                "request_digest": failed_request_digest,
                "request_payload": json.dumps(
                    {
                        "schema": "become.i4-service-request@1",
                        "external_effects": 0,
                    }
                ),
                "receipt_digest": failed_receipt_digest,
                "receipt_payload": failed_payload,
                "source_sha": failed_source_sha,
            },
        )
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
                "workspace_id": workspace_id,
                "correlation_id": correlation_id,
                "failed_slot_id": failed_slot_id,
                "digest_a": failed_request_digest,
                "digest_b": failed_receipt_digest,
                "digest_c": "4" * 64,
                "source_a": failed_source_sha,
                "source_b": "5" * 40,
                "payload": recovery_payload,
            },
        )
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
    return HistoricalRecoveryGraph(
        recovery_id=str(recovery_id),
        workspace_id=str(workspace_id),
        media_intake_id=str(media_intake_id),
        correlation_id=str(correlation_id),
        failed_slot_id=str(failed_slot_id),
        row=tuple(str(value) for value in row),
    )

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


async def _read_recovery_links(
    recovery_id: str,
) -> tuple[str, ...]:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT recovery.id::text, workspace.id::text,
                           intake.id::text, correlation.id::text,
                           failed_slot.id::text, failed_slot.stage,
                           failed_slot.state
                    FROM become_i4_recovery_receipts AS recovery
                    JOIN workspaces AS workspace
                      ON workspace.id = recovery.workspace_id
                    JOIN become_voice_correlations AS correlation
                      ON correlation.id = recovery.correlation_id
                     AND correlation.workspace_id = recovery.workspace_id
                    JOIN become_media_intakes AS intake
                      ON intake.id = correlation.media_intake_id
                     AND intake.workspace_id = recovery.workspace_id
                    JOIN become_voice_chain_slots AS failed_slot
                      ON failed_slot.id = recovery.failed_slot_id
                     AND failed_slot.workspace_id = recovery.workspace_id
                     AND failed_slot.correlation_id = recovery.correlation_id
                    WHERE recovery.id = :id
                    """
                ),
                {"id": recovery_id},
            )
        ).one()
    await engine.dispose()
    return tuple(str(value) for value in row)


async def _read_recovery_fk_validation() -> tuple[tuple[str, bool], ...]:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT conname, convalidated
                    FROM pg_constraint
                    WHERE conname IN (
                        'fk_become_i4_recovery_workspace',
                        'fk_become_i4_recovery_correlation',
                        'fk_become_i4_recovery_failed_slot'
                    )
                    ORDER BY conname
                    """
                )
            )
        ).all()
    await engine.dispose()
    return tuple((str(name), bool(validated)) for name, validated in rows)


def test_populated_historical_schema_upgrade_preserves_recovery_and_quest_rows() -> None:
    _upgrade("0081_become_i4_recovery_v2")
    recovery = asyncio.run(_seed_historical_recovery())
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
            recovery.recovery_id,
            "id::text, schema_name, stage, attempt_version::text, state, "
            "enabled::text, original_request_digest, original_receipt_digest, "
            "receipt_digest, receipt_payload::text",
        )
    )
    recovery_links_after = asyncio.run(
        _read_recovery_links(recovery.recovery_id)
    )
    recovery_fks_after = asyncio.run(_read_recovery_fk_validation())
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
    assert recovery_after == recovery.row
    assert recovery_links_after == (
        recovery.recovery_id,
        recovery.workspace_id,
        recovery.media_intake_id,
        recovery.correlation_id,
        recovery.failed_slot_id,
        "mayaki",
        "failed",
    )
    assert recovery_fks_after == (
        ("fk_become_i4_recovery_correlation", True),
        ("fk_become_i4_recovery_failed_slot", True),
        ("fk_become_i4_recovery_workspace", True),
    )
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
    pilot = Path(
        "alembic/versions/0084_become_pilot_slice1_review.py"
    ).read_text()
    program = Path(
        "alembic/versions/0085_become_pilot_slice2_program.py"
    ).read_text()
    beta_replies = Path(
        "alembic/versions/0088_beta_wedding_replies.py"
    ).read_text()
    assert 'revision: str = "0082_become_quest"' in original
    assert 'down_revision: str | None = "0081_become_i4_recovery_v2"' in original
    assert 'revision: str = "0083_become_quest_corrective"' in corrective
    assert 'down_revision: str | None = "0082_become_quest"' in corrective
    assert "UPDATE become_i4" not in corrective
    assert "DELETE FROM become_i4" not in corrective
    assert "not a safe production rollback" in corrective
    assert 'revision: str = "0084_become_pilot_slice1_review"' in pilot
    assert 'revision: str = "0085_become_pilot_slice2_program"' in program
    assert 'down_revision: str | None = "0084_become_pilot_slice1_review"' in program
    assert 'revision: str = "0088_beta_wedding_replies"' in beta_replies
    assert (
        'down_revision: str | None = "0087_become_pilot_slice3_learner"'
        in beta_replies
    )
    assert (
        'down_revision: str | None = "0083_become_quest_corrective_m"'
        in pilot
    )
    heads = subprocess.run(
        ["alembic", "heads"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": _database_url()},
    ).stdout.strip().splitlines()
    assert len(heads) == 1
    assert heads[0].startswith("0092_aqub_installation_binding")

