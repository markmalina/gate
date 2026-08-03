"""SIMULATED unit proofs for the canonical default-off Quest contract."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.application.become_quest import (
    QUEST_MAX_OPERATIONS,
    QUEST_PROJECTION_ENABLED,
    READINESS_APPROVALS,
    ArtifactType,
    BecomeQuestCoordinator,
    BecomeQuestError,
    QuestState,
    SnapshotQuestStore,
    build_approval_receipt,
    canonical_digest,
    operation_attempt_key,
)

QUEST = "ref://simulated/quest/one"
CORRELATION = "ref://simulated/correlation/one"
EXPERT = "ref://simulated/person/lesha"
LEARNER = "ref://simulated/person/mark"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def command(operation: str, key: str, **fields: object) -> dict[str, object]:
    return {
        "operation": operation,
        "attempt_version": 1,
        "idempotency_key": key,
        "quest_id": QUEST,
        "correlation_id": CORRELATION,
        "occurred_at": "2026-08-03T09:00:00+00:00",
        **fields,
    }


def simulated_quest(*, mode: str = "SIMULATED") -> BecomeQuestCoordinator:
    coordinator = BecomeQuestCoordinator(SnapshotQuestStore())
    coordinator.execute(
        command(
            "CREATE_QUEST",
            "create",
            mode=mode,
            source_provenance_digest="1" * 64,
        )
    )
    return coordinator


def bind_roles(coordinator: BecomeQuestCoordinator) -> None:
    coordinator.execute(
        command(
            "BIND_PARTICIPANT",
            "bind-expert",
            role="EXPERT",
            person_ref=EXPERT,
            provenance_digest="2" * 64,
        )
    )
    coordinator.execute(
        command(
            "BIND_PARTICIPANT",
            "bind-learner",
            role="LEARNER",
            person_ref=LEARNER,
            provenance_digest="3" * 64,
        )
    )


def consent_and_transcript(
    coordinator: BecomeQuestCoordinator,
    *,
    version: int = 1,
    artifact_digest: str = DIGEST_A,
) -> None:
    coordinator.execute(
        command(
            "EXPERT_CONSENT",
            "consent",
            person_ref=EXPERT,
            consent_ref="ref://simulated/consent/expert",
        )
    )
    coordinator.execute(
        command(
            "PROVIDER_TRANSCRIPT_SETTLED",
            f"transcript-{version}",
            artifact_version=version,
            artifact_digest=artifact_digest,
            provenance_digest="4" * 64,
        )
    )


def approve(
    coordinator: BecomeQuestCoordinator,
    artifact_type: ArtifactType,
    version: int,
    artifact_digest: str,
    *,
    key: str,
) -> dict[str, object]:
    return coordinator.execute(
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


def approved_expert_path() -> BecomeQuestCoordinator:
    coordinator = simulated_quest()
    bind_roles(coordinator)
    consent_and_transcript(coordinator)
    approve(
        coordinator,
        ArtifactType.TRANSCRIPT,
        1,
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
        coordinator.execute(
            command(
                "REGISTER_ARTIFACT",
                f"register-{artifact_type.value}",
                artifact_type=artifact_type.value,
                artifact_version=1,
                artifact_digest=artifact_digest,
                provenance_digest="5" * 64,
            )
        )
        approve(
            coordinator,
            artifact_type,
            1,
            artifact_digest,
            key=f"approve-{artifact_type.value}",
        )
    return coordinator


def ready_quest() -> BecomeQuestCoordinator:
    coordinator = approved_expert_path()
    coordinator.execute(command("INVITE_LEARNER", "invite", **invitation_fields()))
    coordinator.execute(command("ACCEPT_LEARNER", "accept", **acceptance_fields()))
    return coordinator


def invitation_fields(**overrides: object) -> dict[str, object]:
    return {
        "learner_person_ref": LEARNER,
        "invitation_ref": "ref://simulated/invitation/learner-v1",
        "invitation_version": 1,
        "invitation_digest": "f" * 64,
        **overrides,
    }


def acceptance_fields(**overrides: object) -> dict[str, object]:
    return {
        **invitation_fields(),
        "acceptance_ref": "ref://simulated/acceptance/learner-v1",
        "acceptance_digest": "9" * 64,
        **overrides,
    }


def test_simulated_contract_matrix_matches_canonical_public_envelope() -> None:
    matrix = json.loads(
        Path("contracts/quest/v1/operation-contract-matrix.json").read_text()
    )
    assert matrix["command_required"] == [
        "quest_id",
        "correlation_id",
        "operation",
        "attempt_version",
        "idempotency_key",
        "occurred_at",
    ]
    assert set(matrix["readiness_approvals"]) == READINESS_APPROVALS
    assert "EXERCISE" not in matrix["readiness_approvals"]


def test_simulated_create_quest_fixture_executes_with_canonical_receipt() -> None:
    matrix = json.loads(
        Path("contracts/quest/v1/operation-contract-matrix.json").read_text()
    )
    fixture = matrix["executable_fixtures"]["create_quest"]
    coordinator = BecomeQuestCoordinator(SnapshotQuestStore())
    receipt = coordinator.execute(fixture["command"])
    assert receipt == fixture["expected_receipt"]
    for field in matrix["receipt_required"]:
        assert field in receipt
    assert coordinator.execute(fixture["command"]) == receipt
    with pytest.raises(BecomeQuestError, match="mutated_replay"):
        coordinator.execute({**fixture["command"], "mode": "PRODUCTION"})


def test_simulated_distinct_roles_and_synthetic_production_guard() -> None:
    coordinator = simulated_quest()
    bind_roles(coordinator)
    snapshot = coordinator.store.load(QUEST)
    assert snapshot is not None
    assert snapshot.expert_person_ref == EXPERT
    assert snapshot.learner_person_ref == LEARNER

    production = simulated_quest(mode="PRODUCTION")
    with pytest.raises(BecomeQuestError, match="synthetic_participant_in_production"):
        production.execute(
            command(
                "BIND_PARTICIPANT",
                "production-synthetic",
                role="LEARNER",
                person_ref=LEARNER,
                provenance_digest="2" * 64,
            )
        )


def test_simulated_provider_settlement_does_not_approve_transcript() -> None:
    coordinator = simulated_quest()
    bind_roles(coordinator)
    consent_and_transcript(coordinator)
    snapshot = coordinator.store.load(QUEST)
    assert snapshot is not None
    assert ArtifactType.TRANSCRIPT.value not in snapshot.approvals
    assert snapshot.state is QuestState.TRANSCRIPT_PENDING_REVIEW


def test_simulated_transcript_versions_are_append_only_and_fail_closed() -> None:
    coordinator = simulated_quest()
    bind_roles(coordinator)
    consent_and_transcript(coordinator, version=2)
    for key, version, artifact_digest, error in (
        ("lower", 1, DIGEST_B, "artifact_version_not_increased"),
        ("same-mutated", 2, DIGEST_B, "artifact_version_mutation"),
        ("same-exact", 2, DIGEST_A, "artifact_version_not_increased"),
    ):
        with pytest.raises(BecomeQuestError, match=error):
            coordinator.execute(
                command(
                    "PROVIDER_TRANSCRIPT_SETTLED",
                    key,
                    artifact_version=version,
                    artifact_digest=artifact_digest,
                    provenance_digest="4" * 64,
                )
            )


def test_simulated_new_transcript_version_preserves_history_and_invalidates_approval() -> None:
    coordinator = simulated_quest()
    bind_roles(coordinator)
    consent_and_transcript(coordinator)
    approve(
        coordinator,
        ArtifactType.TRANSCRIPT,
        1,
        DIGEST_A,
        key="approve-transcript",
    )
    coordinator.execute(
        command(
            "PROVIDER_TRANSCRIPT_SETTLED",
            "transcript-2",
            artifact_version=2,
            artifact_digest=DIGEST_B,
            provenance_digest="4" * 64,
        )
    )
    snapshot = coordinator.store.load(QUEST)
    assert snapshot is not None
    assert len(snapshot.artifacts[ArtifactType.TRANSCRIPT.value]) == 2
    assert ArtifactType.TRANSCRIPT.value not in snapshot.approvals
    assert snapshot.state is QuestState.TRANSCRIPT_PENDING_REVIEW


def test_simulated_acceptance_requires_current_invitation_and_bound_learner() -> None:
    coordinator = approved_expert_path()
    with pytest.raises(BecomeQuestError, match="learner_acceptance_transition_blocked"):
        coordinator.execute(command("ACCEPT_LEARNER", "accept-early", **acceptance_fields()))
    coordinator.execute(command("INVITE_LEARNER", "invite", **invitation_fields()))
    with pytest.raises(BecomeQuestError, match="learner_only"):
        coordinator.execute(
            command(
                "ACCEPT_LEARNER",
                "wrong-learner",
                **acceptance_fields(learner_person_ref="ref://simulated/person/other"),
            )
        )
    with pytest.raises(BecomeQuestError, match="learner_invitation_mismatch"):
        coordinator.execute(
            command(
                "ACCEPT_LEARNER",
                "stale-invitation",
                **acceptance_fields(invitation_digest="0" * 64),
            )
        )


def test_simulated_invitation_is_immutable_and_versioned() -> None:
    coordinator = approved_expert_path()
    coordinator.execute(command("INVITE_LEARNER", "invite-v1", **invitation_fields()))
    with pytest.raises(BecomeQuestError, match="invitation_version_mutation"):
        coordinator.execute(
            command(
                "INVITE_LEARNER",
                "invite-v1-mutated",
                **invitation_fields(invitation_digest="0" * 64),
            )
        )
    coordinator.execute(
        command(
            "INVITE_LEARNER",
            "invite-v2",
            **invitation_fields(
                invitation_ref="ref://simulated/invitation/learner-v2",
                invitation_version=2,
                invitation_digest="0" * 64,
            ),
        )
    )
    snapshot = coordinator.store.load(QUEST)
    assert snapshot is not None
    assert len(snapshot.invitations) == 2


def test_simulated_four_approvals_reach_ready_without_exercise_approval() -> None:
    coordinator = approved_expert_path()
    coordinator.execute(command("INVITE_LEARNER", "invite", **invitation_fields()))
    receipt = coordinator.execute(
        command("ACCEPT_LEARNER", "accept", **acceptance_fields())
    )
    snapshot = coordinator.store.load(QUEST)
    assert snapshot is not None
    assert receipt["state"] == QuestState.READY_FOR_EXERCISE.value
    assert ArtifactType.EXERCISE.value not in snapshot.approvals
    assert receipt["external_effects"] == 0


def test_simulated_profile_and_program_versions_recompute_readiness() -> None:
    coordinator = ready_quest()
    for artifact_type, digest, key in (
        (ArtifactType.EXPERT_PROFILE, "6" * 64, "profile-v2"),
        (ArtifactType.PROGRAM, "7" * 64, "program-v2"),
    ):
        before = coordinator.store.load(QUEST)
        assert before is not None
        history_count = len(before.approval_history)
        coordinator.execute(
            command(
                "REGISTER_ARTIFACT",
                key,
                artifact_type=artifact_type.value,
                artifact_version=2,
                artifact_digest=digest,
                provenance_digest="8" * 64,
            )
        )
        pending = coordinator.store.load(QUEST)
        assert pending is not None
        assert pending.state is not QuestState.READY_FOR_EXERCISE
        assert artifact_type.value not in pending.approvals
        assert len(pending.approval_history) == history_count
        approve(
            coordinator,
            artifact_type,
            2,
            digest,
            key=f"approve-{key}",
        )
        restored = coordinator.store.load(QUEST)
        assert restored is not None
        assert restored.state is QuestState.READY_FOR_EXERCISE

    coordinator.execute(
        command(
            "REGISTER_ARTIFACT",
            "exercise-v1",
            artifact_type=ArtifactType.EXERCISE.value,
            artifact_version=1,
            artifact_digest="e" * 64,
            provenance_digest="8" * 64,
        )
    )
    exercise = coordinator.store.load(QUEST)
    assert exercise is not None
    assert exercise.state is QuestState.READY_FOR_EXERCISE


def test_simulated_exact_binding_is_noop_after_ready() -> None:
    coordinator = ready_quest()
    for role, person_ref, digest in (
        ("EXPERT", EXPERT, "2" * 64),
        ("LEARNER", LEARNER, "3" * 64),
    ):
        receipt = coordinator.execute(
            command(
                "BIND_PARTICIPANT",
                f"rebind-{role}",
                role=role,
                person_ref=person_ref,
                provenance_digest=digest,
            )
        )
        assert receipt["state"] == QuestState.READY_FOR_EXERCISE.value
    with pytest.raises(BecomeQuestError, match="role_rebinding_forbidden"):
        coordinator.execute(
            command(
                "BIND_PARTICIPANT",
                "rebind-changed",
                role="EXPERT",
                person_ref="ref://simulated/person/other",
                provenance_digest="2" * 64,
            )
        )


def test_simulated_exact_and_mutated_replay_are_scoped() -> None:
    coordinator = simulated_quest()
    request = command(
        "BIND_PARTICIPANT",
        "shared-key",
        role="EXPERT",
        person_ref=EXPERT,
        provenance_digest="2" * 64,
    )
    first = coordinator.execute(request)
    assert coordinator.execute(request) == first
    with pytest.raises(BecomeQuestError, match="mutated_replay"):
        coordinator.execute({**request, "provenance_digest": "3" * 64})

    first_scope = operation_attempt_key(
        quest_id=QUEST,
        correlation_id=CORRELATION,
        operation="BIND_PARTICIPANT",
        attempt_version=1,
        idempotency_key="shared-key",
    )
    second_scope = operation_attempt_key(
        quest_id="ref://simulated/quest/two",
        correlation_id="ref://simulated/correlation/two",
        operation="BIND_PARTICIPANT",
        attempt_version=1,
        idempotency_key="shared-key",
    )
    assert first_scope != second_scope


def test_simulated_snapshot_store_is_explicitly_test_only() -> None:
    assert SnapshotQuestStore.test_fixture_only is True
    store = SnapshotQuestStore()
    first = BecomeQuestCoordinator(store)
    create = command(
        "CREATE_QUEST",
        "create",
        mode="SIMULATED",
        source_provenance_digest="1" * 64,
    )
    receipt = first.execute(create)
    restarted = BecomeQuestCoordinator(SnapshotQuestStore(store.snapshot()))
    assert restarted.execute(create) == receipt


def test_simulated_approval_receipt_is_versioned_and_content_free() -> None:
    receipt = build_approval_receipt(
        quest_id=QUEST,
        artifact_type=ArtifactType.TRANSCRIPT,
        artifact_version=1,
        artifact_digest=DIGEST_A,
        approver_person_ref=EXPERT,
        approved_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    assert receipt["schema"] == "become.quest-approval@1"
    assert receipt["external_effects"] == 0
    assert canonical_digest(receipt)


def test_simulated_sensitive_payload_fails_and_all_effect_gates_are_off() -> None:
    coordinator = BecomeQuestCoordinator(SnapshotQuestStore())
    with pytest.raises(BecomeQuestError, match="sensitive_field_forbidden"):
        coordinator.execute(
            {
                **command(
                    "CREATE_QUEST",
                    "create",
                    mode="SIMULATED",
                    source_provenance_digest="1" * 64,
                ),
                "transcript_text": "forbidden",
            }
        )
    assert QUEST_PROJECTION_ENABLED is False
    assert QUEST_MAX_OPERATIONS == 0
