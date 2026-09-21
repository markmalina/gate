"""Non-skipped PostgreSQL release proofs for Become Slice 3."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.become_learner_invitation import (
    CONSENT_POLICY_DIGEST,
    CONSENT_POLICY_VERSION,
    PROGRAM_DIGEST,
    PROGRAM_ID,
    PinInvitationCommand,
    Slice3Disposition,
    Slice3Error,
    learner_binding_digest,
)
from app.infrastructure.become_learner_invitation import (
    SessionFactoryLearnerInvitationStore,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from test_become_quest_migration import _seed_historical_recovery
from test_become_transcript_review_postgres import (
    PilotGraph,
    _clean_pilot_fixtures,
    seed_pilot,
)

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
SPACE = "space://become/quest-owner-pilot"
INSTALLATION = "installation://telegram/BecomeNowBot"


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.fail("DATABASE_URL is mandatory; Slice 3 PostgreSQL tests may not skip")
    return value


@pytest.fixture(scope="module", autouse=True)
def migrated_schema() -> Iterator[None]:
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DATABASE_URL": _database_url()},
    )
    yield
    if os.environ.get("BECOME_SLICE3_PRESERVE_RESTORE_GRAPH") != "1":
        subprocess.run(
            ["alembic", "downgrade", "base"],
            check=True,
            env={**os.environ, "DATABASE_URL": _database_url()},
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def sessions() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _clean_slice3_rows(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as session:
        for table in (
            "become_slice3_relations_outbox",
            "become_slice3_deliveries",
            "become_slice3_acceptances",
            "become_slice3_invitations",
            "become_slice3_pilot_slots",
            "become_slice3_learner_candidates",
            "become_pilot_program_deliveries",
            "become_pilot_program_versions",
            "become_pilot_program_drafts",
        ):
            await session.execute(text(f"DELETE FROM {table}"))
    await _clean_pilot_fixtures(sessions)


@pytest.fixture(autouse=True)
async def clean_slice3(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    await _clean_slice3_rows(sessions)
    yield
    if os.environ.get("BECOME_SLICE3_PRESERVE_RESTORE_GRAPH") != "1":
        await _clean_slice3_rows(sessions)


async def _receipt(session: AsyncSession, payload: dict[str, object]) -> UUID:
    receipt_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO telegram_receipts "
            "(id,bot_key,update_id,payload,status,received_at,processed_at) VALUES "
            "(:id,'telegram_become_group',:update,CAST(:payload AS jsonb),"
            "'processed',:now,:now)"
        ),
        {
            "id": receipt_id,
            "update": receipt_id.int % 9_000_000_000_000_000_000,
            "payload": json.dumps(payload),
            "now": NOW,
        },
    )
    return receipt_id


async def _seed_program(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[PilotGraph, SessionFactoryLearnerInvitationStore]:
    graph = await seed_pilot(sessions, with_review=True)
    assert graph.review_id is not None
    async with sessions.begin() as session:
        source_transcript = (
            await session.execute(
                text(
                    "SELECT review.latest_version AS version,version.transcript_digest "
                    "FROM become_pilot_transcript_reviews AS review "
                    "JOIN become_pilot_transcript_versions AS version "
                    "ON version.review_id=review.id AND version.version=review.latest_version "
                    "WHERE review.id=:id"
                ),
                {"id": graph.review_id},
            )
        ).mappings().one()
        await session.execute(
            text(
                "INSERT INTO become_pilot_program_drafts "
                "(id,workspace_id,transcript_review_id,source_transcript_version,"
                "source_transcript_digest,dialog_id,subject_ref,state,latest_version,"
                "callback_ref,approved_version,approved_digest,approved_at,created_at,"
                "updated_at) VALUES (:id,:workspace,:review,:source_version,"
                ":source_digest,:dialog,:subject,'approved',2,:callback,2,:digest,"
                ":now,:now,:now)"
            ),
            {
                "id": UUID(PROGRAM_ID),
                "workspace": graph.workspace_id,
                "review": graph.review_id,
                "source_version": source_transcript["version"],
                "source_digest": source_transcript["transcript_digest"],
                "dialog": graph.dialog_id,
                "subject": graph.subject_ref,
                "callback": uuid4(),
                "digest": PROGRAM_DIGEST,
                "now": NOW,
            },
        )
        await session.execute(
            text(
                "INSERT INTO become_pilot_program_versions "
                "(id,draft_id,version,source,program_body,program_digest,expires_at,"
                "created_at) VALUES (:id,:draft,2,'human_correction',:body,:digest,"
                ":expires,:now)"
            ),
            {
                "id": uuid4(),
                "draft": UUID(PROGRAM_ID),
                "body": "private exact PROGRAM fixture",
                "digest": PROGRAM_DIGEST,
                "expires": NOW + timedelta(days=30),
                "now": NOW,
            },
        )
    return graph, SessionFactoryLearnerInvitationStore(sessions)


async def _candidate(
    sessions: async_sessionmaker[AsyncSession],
    store: SessionFactoryLearnerInvitationStore,
    graph: PilotGraph,
    actor: str,
) -> str:
    binding = learner_binding_digest(
        telegram_actor_id=actor,
        private_dialog_id=actor,
        bot_installation_identity=INSTALLATION,
        become_space_ref=SPACE,
    )
    async with sessions.begin() as session:
        receipt_id = await _receipt(session, {"slice3": "candidate"})
    await store.register_candidate(
        workspace_key=graph.workspace_key,
        source_receipt_id=str(receipt_id),
        subject_ref=f"subject://telegram/{actor}",
        telegram_actor_id=actor,
        private_dialog_id=actor,
        bot_installation_identity=INSTALLATION,
        become_space_ref=SPACE,
        learner_binding_digest=binding,
        now=NOW,
    )
    return binding


def _pin(graph: PilotGraph, binding: str) -> PinInvitationCommand:
    return PinInvitationCommand(
        workspace_key=graph.workspace_key,
        source_receipt_id=str(graph.media_receipt_id),
        expert_subject_ref=graph.subject_ref,
        expert_dialog_id=graph.provider_dialog_id,
        learner_binding_digest=binding,
        become_space_ref=SPACE,
        bot_installation_identity=INSTALLATION,
    )


async def _delivered(
    sessions: async_sessionmaker[AsyncSession],
    store: SessionFactoryLearnerInvitationStore,
    graph: PilotGraph,
    actor: str = "2001",
) -> tuple[str, str, UUID]:
    binding = await _candidate(sessions, store, graph, actor)
    invitation = await store.pin_and_create_invitation(command=_pin(graph, binding), now=NOW)
    job = await store.claim_delivery(now=NOW, max_sends=2)
    assert job is not None and job.kind == "invitation"
    await store.settle_delivery(
        delivery_id=job.delivery_id,
        outcome="sent",
        telegram_message_id="1",
        now=NOW,
    )
    async with sessions.begin() as session:
        callback_ref = await session.scalar(
            text(
                "SELECT callback_ref::text FROM become_slice3_invitations WHERE id=:id"
            ),
            {"id": UUID(invitation.invitation_id)},
        )
        receipt_id = await _receipt(session, {"slice3": "accept"})
    assert callback_ref is not None
    return binding, str(callback_ref).replace("-", ""), receipt_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("column", "mutated_value"),
    [
        ("telegram_actor_id", "2999"),
        ("private_dialog_id", "2999"),
        ("bot_installation_identity", "installation://telegram/rebound"),
        ("become_space_ref", "space://become/rebound"),
    ],
)
async def test_postgres_pin_rejects_candidate_tuple_mutation_before_slot_reservation(
    sessions: async_sessionmaker[AsyncSession],
    column: str,
    mutated_value: str,
) -> None:
    graph, store = await _seed_program(sessions)
    binding = await _candidate(sessions, store, graph, "2001")
    async with sessions.begin() as session:
        await session.execute(
            text(
                f"UPDATE become_slice3_learner_candidates SET {column}=:value "
                "WHERE learner_binding_digest=:digest"
            ),
            {"value": mutated_value, "digest": binding},
        )
    with pytest.raises(Slice3Error, match="slice3_candidate_binding_mutated"):
        await store.pin_and_create_invitation(command=_pin(graph, binding), now=NOW)
    async with sessions() as session:
        assert await session.scalar(text("SELECT count(*) FROM become_slice3_pilot_slots")) == 0
        assert await session.scalar(text("SELECT count(*) FROM become_slice3_invitations")) == 0


@pytest.mark.anyio
async def test_postgres_two_candidate_pin_race_and_restart_replay(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph, store = await _seed_program(sessions)
    first, second = await asyncio.gather(
        _candidate(sessions, store, graph, "2001"),
        _candidate(sessions, store, graph, "2002"),
    )
    results = await asyncio.gather(
        store.pin_and_create_invitation(command=_pin(graph, first), now=NOW),
        store.pin_and_create_invitation(command=_pin(graph, second), now=NOW),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(isinstance(value, Slice3Error) for value in results) == 1
    winner = first if not isinstance(results[0], Exception) else second
    restarted = SessionFactoryLearnerInvitationStore(sessions)
    replay = await restarted.pin_and_create_invitation(command=_pin(graph, winner), now=NOW)
    assert replay.replayed is True
    async with sessions() as session:
        assert await session.scalar(text("SELECT count(*) FROM become_slice3_pilot_slots")) == 1
        assert await session.scalar(text("SELECT count(*) FROM become_slice3_invitations")) == 1


@pytest.mark.anyio
async def test_postgres_two_callback_race_commits_one_acceptance_and_replays_after_restart(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph, store = await _seed_program(sessions)
    _, callback_ref, receipt_id = await _delivered(sessions, store, graph)

    async def accept(current: SessionFactoryLearnerInvitationStore):
        return await current.apply_learner_callback(
            action="a",
            callback_ref=callback_ref,
            subject_ref="subject://telegram/2001",
            private_dialog_id="2001",
            source_receipt_id=str(receipt_id),
            consent_policy_version=CONSENT_POLICY_VERSION,
            consent_policy_digest=CONSENT_POLICY_DIGEST,
            now=NOW,
        )

    first, second = await asyncio.gather(accept(store), accept(store))
    assert {first.disposition, second.disposition} == {
        Slice3Disposition.ACCEPTED,
        Slice3Disposition.ACCEPTED_DUPLICATE,
    }
    replay = await accept(SessionFactoryLearnerInvitationStore(sessions))
    assert replay.disposition is Slice3Disposition.ACCEPTED_DUPLICATE
    async with sessions() as session:
        assert await session.scalar(text("SELECT count(*) FROM become_slice3_acceptances")) == 1
        assert (
            await session.scalar(
                text("SELECT count(*) FROM become_slice3_relations_outbox")
            )
            == 1
        )
        assert await session.scalar(
            text("SELECT count(*) FROM become_slice3_deliveries WHERE kind='confirmation'")
        ) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "program_correction",
        "program_purge",
        "candidate_rebind",
        "slot_mutation",
        "expert_dialog_rebind",
        "program_dialog_mutation",
    ],
)
async def test_postgres_callback_revalidates_every_locked_binding(
    sessions: async_sessionmaker[AsyncSession], mutation: str
) -> None:
    graph, store = await _seed_program(sessions)
    _, callback_ref, receipt_id = await _delivered(sessions, store, graph)
    async with sessions.begin() as session:
        if mutation == "program_correction":
            await session.execute(
                text(
                    "UPDATE become_pilot_program_drafts SET state='invalidated',"
                    "approved_version=NULL,approved_digest=NULL,approved_at=NULL,"
                    "invalidated_at=:now WHERE id=:id"
                ),
                {"id": UUID(PROGRAM_ID), "now": NOW},
            )
        elif mutation == "program_purge":
            await session.execute(
                text(
                    "UPDATE become_pilot_program_versions SET program_body=NULL,"
                    "purged_at=:now WHERE draft_id=:id AND version=2"
                ),
                {"id": UUID(PROGRAM_ID), "now": NOW},
            )
        elif mutation == "candidate_rebind":
            await session.execute(
                text(
                    "UPDATE become_slice3_learner_candidates SET telegram_actor_id='9999'"
                )
            )
        elif mutation == "slot_mutation":
            await session.execute(
                text(
                    "UPDATE become_slice3_pilot_slots SET learner_binding_digest=:digest"
                ),
                {"digest": "f" * 64},
            )
        elif mutation == "expert_dialog_rebind":
            await session.execute(
                text(
                    "UPDATE dialogs SET provider_dialog_id='expert-dialog-rebound' "
                    "WHERE id=(SELECT dialog_id FROM become_pilot_program_drafts "
                    "WHERE id=:program_id)"
                ),
                {"program_id": UUID(PROGRAM_ID)},
            )
        else:
            rebound_dialog_id = uuid4()
            await session.execute(
                text(
                    "INSERT INTO dialogs "
                    "(id,workspace_id,connector_id,provider_dialog_id,dialog_kind,"
                    "created_at,updated_at) "
                    "SELECT :id,workspace_id,connector_id,'program-dialog-rebound',"
                    "dialog_kind,:now,:now FROM dialogs WHERE id=:source_dialog_id"
                ),
                {
                    "id": rebound_dialog_id,
                    "source_dialog_id": graph.dialog_id,
                    "now": NOW,
                },
            )
            await session.execute(
                text(
                    "UPDATE become_pilot_program_drafts SET dialog_id=:dialog_id "
                    "WHERE id=:program_id"
                ),
                {
                    "dialog_id": rebound_dialog_id,
                    "program_id": UUID(PROGRAM_ID),
                },
            )
    with pytest.raises(Slice3Error):
        await store.apply_learner_callback(
            action="a",
            callback_ref=callback_ref,
            subject_ref="subject://telegram/2001",
            private_dialog_id="2001",
            source_receipt_id=str(receipt_id),
            consent_policy_version=CONSENT_POLICY_VERSION,
            consent_policy_digest=CONSENT_POLICY_DIGEST,
            now=NOW,
        )
    async with sessions() as session:
        assert await session.scalar(text("SELECT count(*) FROM become_slice3_acceptances")) == 0
        assert (
            await session.scalar(
                text("SELECT count(*) FROM become_slice3_relations_outbox")
            )
            == 0
        )


@pytest.mark.anyio
async def test_postgres_expiry_cleanup_status_and_rollback_preserve_hashes(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph, store = await _seed_program(sessions)
    binding = await _candidate(sessions, store, graph, "2001")
    invitation = await store.pin_and_create_invitation(command=_pin(graph, binding), now=NOW)
    async with sessions.begin() as session:
        await session.execute(
            text("UPDATE become_slice3_invitations SET expires_at=:past WHERE id=:id"),
            {"past": NOW - timedelta(seconds=1), "id": UUID(invitation.invitation_id)},
        )
    cleanup = await store.purge_expired(now=NOW)
    assert cleanup["expired_invitation_count"] == 1
    assert cleanup["candidate_tombstone_count"] == 1
    again = await store.purge_expired(now=NOW)
    assert again["expired_invitation_count"] == 0
    status = await store.status(now=NOW)
    assert status["candidate_tombstone_count"] == 1
    assert status["content_exposed"] is False
    preflight = await store.rollback_preflight()
    assert preflight["schema_downgrade_allowed"] is True
    async with sessions() as session:
        candidate = (
            await session.execute(
                text(
                    "SELECT c.telegram_actor_id,c.private_dialog_id,i.callback_digest,"
                    "c.learner_binding_digest FROM become_slice3_learner_candidates c "
                    "JOIN become_slice3_invitations i ON i.candidate_id=c.id"
                )
            )
        ).mappings().one()
        assert candidate["telegram_actor_id"] is None
        assert candidate["private_dialog_id"] is None
        assert candidate["learner_binding_digest"] == binding
        assert len(candidate["callback_digest"]) == 64


@pytest.mark.anyio
async def test_postgres_accepted_evidence_blocks_downgrade_but_allows_app_rollback(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph, store = await _seed_program(sessions)
    _, callback_ref, receipt_id = await _delivered(sessions, store, graph)
    await store.apply_learner_callback(
        action="a",
        callback_ref=callback_ref,
        subject_ref="subject://telegram/2001",
        private_dialog_id="2001",
        source_receipt_id=str(receipt_id),
        consent_policy_version=CONSENT_POLICY_VERSION,
        consent_policy_digest=CONSENT_POLICY_DIGEST,
        now=NOW,
    )
    preflight = await store.rollback_preflight()
    assert preflight["accepted_count"] == 1
    assert preflight["schema_downgrade_allowed"] is False
    assert preflight["application_rollback_allowed"] is True


@pytest.mark.anyio
async def test_slice3_migration_is_single_head_and_leaves_populated_restore_graph(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    if os.environ.get("BECOME_SLICE3_PRESERVE_RESTORE_GRAPH") == "1":
        await _seed_historical_recovery()
    graph, store = await _seed_program(sessions)
    _, callback_ref, receipt_id = await _delivered(sessions, store, graph)
    await store.apply_learner_callback(
        action="a",
        callback_ref=callback_ref,
        subject_ref="subject://telegram/2001",
        private_dialog_id="2001",
        source_receipt_id=str(receipt_id),
        consent_policy_version=CONSENT_POLICY_VERSION,
        consent_policy_digest=CONSENT_POLICY_DIGEST,
        now=NOW,
    )
    heads = subprocess.run(
        ["alembic", "heads"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": _database_url()},
    ).stdout.strip().splitlines()
    assert len(heads) == 1
    assert heads[0].startswith("0107_no_speech_recovery")
    async with sessions() as session:
        assert await session.scalar(text("SELECT count(*) FROM become_slice3_invitations")) == 1
        assert await session.scalar(text("SELECT count(*) FROM become_slice3_acceptances")) == 1

