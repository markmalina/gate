"""SIMULATED proofs for the default-off role-correct BECOME Quest."""

from datetime import UTC, datetime

import pytest

from app.application.become_quest import (
    QUEST_MAX_OPERATIONS,
    QUEST_PROJECTION_ENABLED,
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
        "idempotency_key": key,
        "quest_ref": QUEST,
        "correlation_ref": CORRELATION,
        **fields,
    }


def simulated_quest(*, mode: str = "SIMULATED") -> BecomeQuestCoordinator:
    coordinator = BecomeQuestCoordinator(SnapshotQuestStore())
    coordinator.execute(command("CREATE_QUEST", "create", mode=mode))
    return coordinator


def bind_simulated_roles(coordinator: BecomeQuestCoordinator) -> None:
    coordinator.execute(
        command(
            "BIND_PARTICIPANT",
            "bind-expert",
            role="EXPERT",
            person_ref=EXPERT,
        )
    )
    coordinator.execute(
        command(
            "BIND_PARTICIPANT",
            "bind-learner",
            role="LEARNER",
            person_ref=LEARNER,
        )
    )


def approve_simulated(
    coordinator: BecomeQuestCoordinator,
    artifact_type: ArtifactType,
    version: int,
    digest: str,
    *,
    key: str,
) -> dict[str, object]:
    return coordinator.execute(
        command(
            "APPROVE_ARTIFACT",
            key,
            artifact_type=artifact_type.value,
            artifact_version=version,
            artifact_digest=digest,
            approver_person_ref=EXPERT,
            approver_role="EXPERT",
        )
    )


def approved_simulated_expert_path() -> BecomeQuestCoordinator:
    coordinator = simulated_quest()
    bind_simulated_roles(coordinator)
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
            "transcript",
            artifact_version=1,
            artifact_digest=DIGEST_A,
        )
    )
    approve_simulated(
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
        digest = f"{index:x}" * 64
        coordinator.execute(
            command(
                "REGISTER_ARTIFACT",
                f"register-{artifact_type.value}",
                artifact_type=artifact_type.value,
                artifact_version=1,
                artifact_digest=digest,
            )
        )
        approve_simulated(
            coordinator,
            artifact_type,
            1,
            digest,
            key=f"approve-{artifact_type.value}",
        )
    return coordinator


def test_simulated_distinct_expert_and_learner_are_accepted() -> None:
    coordinator = simulated_quest()
    bind_simulated_roles(coordinator)

    snapshot = coordinator.store.load(QUEST)

    assert snapshot is not None
    assert snapshot.expert_person_ref == EXPERT
    assert snapshot.learner_person_ref == LEARNER


def test_simulated_one_person_cannot_hold_both_roles() -> None:
    coordinator = simulated_quest()
    coordinator.execute(
        command(
            "BIND_PARTICIPANT",
            "bind-expert",
            role="EXPERT",
            person_ref=EXPERT,
        )
    )

    with pytest.raises(BecomeQuestError, match="roles_must_use_distinct_people"):
        coordinator.execute(
            command(
                "BIND_PARTICIPANT",
                "bind-learner",
                role="LEARNER",
                person_ref=EXPERT,
            )
        )


def test_simulated_fixture_is_rejected_by_production_mode() -> None:
    coordinator = simulated_quest(mode="PRODUCTION")

    with pytest.raises(
        BecomeQuestError,
        match="synthetic_participant_in_production",
    ):
        coordinator.execute(
            command(
                "BIND_PARTICIPANT",
                "bind-expert",
                role="EXPERT",
                person_ref=EXPERT,
            )
        )


def test_simulated_provider_settlement_does_not_approve_transcript() -> None:
    coordinator = simulated_quest()
    bind_simulated_roles(coordinator)
    coordinator.execute(
        command(
            "EXPERT_CONSENT",
            "consent",
            person_ref=EXPERT,
            consent_ref="ref://simulated/consent/expert",
        )
    )
    receipt = coordinator.execute(
        command(
            "PROVIDER_TRANSCRIPT_SETTLED",
            "transcript",
            artifact_version=1,
            artifact_digest=DIGEST_A,
        )
    )

    snapshot = coordinator.store.load(QUEST)
    assert receipt["state"] == QuestState.TRANSCRIPT_PENDING_REVIEW.value
    assert snapshot is not None
    assert ArtifactType.TRANSCRIPT.value not in snapshot.approvals


def test_simulated_approval_of_other_artifact_version_fails_closed() -> None:
    coordinator = approved_simulated_expert_path()
    coordinator.execute(
        command(
            "REGISTER_ARTIFACT",
            "product-v2",
            artifact_type=ArtifactType.PRODUCT.value,
            artifact_version=2,
            artifact_digest=DIGEST_B,
        )
    )

    with pytest.raises(
        BecomeQuestError,
        match="approval_artifact_version_mismatch",
    ):
        approve_simulated(
            coordinator,
            ArtifactType.PRODUCT,
            1,
            "3" * 64,
            key="stale-product-approval",
        )


def test_simulated_non_expert_cannot_approve() -> None:
    coordinator = simulated_quest()
    bind_simulated_roles(coordinator)
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
            "transcript",
            artifact_version=1,
            artifact_digest=DIGEST_A,
        )
    )

    with pytest.raises(
        BecomeQuestError,
        match="approval_requires_quest_expert",
    ):
        coordinator.execute(
            command(
                "APPROVE_ARTIFACT",
                "learner-approval",
                artifact_type=ArtifactType.TRANSCRIPT.value,
                artifact_version=1,
                artifact_digest=DIGEST_A,
                approver_person_ref=LEARNER,
                approver_role="LEARNER",
            )
        )


def test_simulated_exact_join_replay_is_idempotent_and_mutation_is_rejected() -> None:
    coordinator = simulated_quest()
    request = command(
        "BIND_PARTICIPANT",
        "bind-expert",
        role="EXPERT",
        person_ref=EXPERT,
    )
    first = coordinator.execute(request)
    replay = coordinator.execute(request)

    assert replay == first
    with pytest.raises(BecomeQuestError, match="mutated_replay"):
        coordinator.execute({**request, "person_ref": LEARNER})


def test_simulated_new_quest_has_independent_attempt_scope() -> None:
    first = operation_attempt_key(
        quest_ref=QUEST,
        correlation_ref=CORRELATION,
        operation_type="APPROVE_TRANSCRIPT",
        attempt_version=5,
    )
    second = operation_attempt_key(
        quest_ref="ref://simulated/quest/two",
        correlation_ref="ref://simulated/correlation/two",
        operation_type="APPROVE_TRANSCRIPT",
        attempt_version=1,
    )

    assert first != second


def test_simulated_snapshot_survives_process_restart() -> None:
    store = SnapshotQuestStore()
    first_process = BecomeQuestCoordinator(store)
    first_process.execute(command("CREATE_QUEST", "create", mode="SIMULATED"))
    first_process.execute(
        command(
            "BIND_PARTICIPANT",
            "bind-expert",
            role="EXPERT",
            person_ref=EXPERT,
        )
    )

    restarted = BecomeQuestCoordinator(SnapshotQuestStore(store.snapshot()))
    snapshot = restarted.store.load(QUEST)

    assert snapshot is not None
    assert snapshot.expert_person_ref == EXPERT
    assert (
        restarted.execute(
            command(
                "BIND_PARTICIPANT",
                "bind-expert",
                role="EXPERT",
                person_ref=EXPERT,
            )
        )["state"]
        == QuestState.EXPERT_BOUND.value
    )


def test_simulated_learner_acceptance_cannot_be_skipped() -> None:
    coordinator = approved_simulated_expert_path()

    with pytest.raises(
        BecomeQuestError,
        match="learner_acceptance_transition_blocked",
    ):
        coordinator.execute(
            command(
                "ACCEPT_LEARNER",
                "accept",
                learner_person_ref=LEARNER,
                acceptance_ref="ref://simulated/acceptance/learner",
            )
        )


def test_simulated_full_human_gate_reaches_ready_without_external_effects() -> None:
    coordinator = approved_simulated_expert_path()
    invitation = coordinator.execute(
        command(
            "INVITE_LEARNER",
            "invite",
            learner_person_ref=LEARNER,
            invitation_ref="ref://simulated/invitation/learner",
        )
    )
    enrolled = coordinator.execute(
        command(
            "ACCEPT_LEARNER",
            "accept",
            learner_person_ref=LEARNER,
            acceptance_ref="ref://simulated/acceptance/learner",
        )
    )

    assert invitation["external_effects"] == 0
    assert enrolled["external_effects"] == 0
    assert enrolled["state"] == QuestState.READY_FOR_EXERCISE.value


def test_simulated_approval_receipt_is_versioned_and_content_free() -> None:
    receipt = build_approval_receipt(
        quest_ref=QUEST,
        artifact_type=ArtifactType.TRANSCRIPT,
        artifact_version=1,
        artifact_digest=DIGEST_A,
        approver_person_ref=EXPERT,
        approved_at=datetime(2026, 8, 3, tzinfo=UTC),
    )

    assert receipt["schema"] == "become.quest-approval@1"
    assert receipt["artifact_version"] == 1
    assert receipt["artifact_digest"] == DIGEST_A
    assert receipt["external_effects"] == 0
    assert canonical_digest(receipt)


def test_simulated_sensitive_payload_fails_closed_and_all_gates_are_off() -> None:
    coordinator = BecomeQuestCoordinator(SnapshotQuestStore())

    with pytest.raises(BecomeQuestError, match="sensitive_field_forbidden"):
        coordinator.execute(
            {
                **command("CREATE_QUEST", "create", mode="SIMULATED"),
                "transcript_text": "forbidden",
            }
        )
    assert QUEST_PROJECTION_ENABLED is False
    assert QUEST_MAX_OPERATIONS == 0
