"""Non-skipped PostgreSQL safety proofs for BECOME Pilot Slice 1."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
    opaque_subject_ref,
)
from app.application.become_media_intake import (
    DownloadedBecomeMedia,
    ProcessBecomeMediaCommand,
    ProcessBecomeMediaService,
)
from app.application.become_transcript_review import (
    PilotReviewDisposition,
    request_digest,
)
from app.application.become_voice_transcription import (
    BecomeTranscriptionBudget,
    BecomeVoiceNormalizer,
    BecomeVoiceTranscriptionStore,
    ProcessBecomeVoiceTranscriptionService,
)
from app.application.billing import (
    BillingProduct,
    BudgetAdmission,
    BudgetReservation,
    BudgetReservationState,
    UsageAttribution,
    UsageOperation,
)
from app.application.telegram_processing import (
    AcceptTelegramReceiptCommand,
    AcceptTelegramReceiptService,
    ProcessTelegramOutboxService,
    TelegramReceiptError,
    TelegramWorkerDisposition,
    canonical_telegram_payload_digest,
)
from app.application.telegram_voice_transcription import (
    NormalizedVoice,
    ProviderTranscript,
    SpeechToTextPort,
)
from app.infrastructure.become_media_intake import (
    SessionFactoryBecomeMediaRetention,
    SessionFactoryBecomeMediaStore,
)
from app.infrastructure.become_transcript_review import (
    SessionFactoryPilotTranscriptReviewStore,
)
from app.infrastructure.become_voice_transcription import (
    SessionFactoryBecomeVoiceTranscriptionStore,
)
from app.infrastructure.telegram_processing import (
    SqlTelegramProcessingQueue,
    SqlTelegramReceiptStore,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

NOW = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
SUBJECT = "ref://pilot/subject/exact"
CHAT = "pilot-private-chat"
DIGEST_1 = "1" * 64
DIGEST_2 = "2" * 64
EVIDENCE = "e" * 64


@dataclass(frozen=True)
class PilotGraph:
    workspace_id: UUID
    workspace_key: str
    dialog_id: UUID
    media_id: UUID
    correlation_id: UUID
    attempt_id: UUID
    review_id: UUID | None
    callback_ref: str | None
    delivery_id: UUID | None
    media_receipt_id: UUID
    subject_ref: str
    provider_dialog_id: str


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.fail("DATABASE_URL is mandatory; Pilot PostgreSQL tests may not skip")
    return value


@pytest.fixture(scope="module", autouse=True)
def migrated_schema() -> None:
    subprocess.run(
        ["alembic", "upgrade", "head"],
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


async def _clean_pilot_fixtures(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    workspace_filter = (
        "SELECT id FROM workspaces "
        "WHERE key LIKE 'pilot-postgres-%'"
    )
    statements = (
        """
        DELETE FROM become_pilot_program_deliveries
        WHERE draft_id IN (
          SELECT id FROM become_pilot_program_drafts
          WHERE workspace_id IN ("""
        + workspace_filter
        + """))
        """,
        """
        DELETE FROM become_pilot_program_versions
        WHERE draft_id IN (
          SELECT id FROM become_pilot_program_drafts
          WHERE workspace_id IN ("""
        + workspace_filter
        + """))
        """,
        "DELETE FROM become_pilot_program_drafts "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        """
        DELETE FROM become_pilot_delivery_reconciliations
        WHERE delivery_id IN (
          SELECT d.id
          FROM become_pilot_transcript_deliveries AS d
          JOIN become_pilot_transcript_reviews AS r ON r.id = d.review_id
          WHERE r.workspace_id IN ("""
        + workspace_filter
        + """))
        """,
        """
        DELETE FROM become_pilot_transcript_deliveries
        WHERE review_id IN (
          SELECT id FROM become_pilot_transcript_reviews
          WHERE workspace_id IN ("""
        + workspace_filter
        + """))
        """,
        """
        DELETE FROM become_pilot_transcript_versions
        WHERE review_id IN (
          SELECT id FROM become_pilot_transcript_reviews
          WHERE workspace_id IN ("""
        + workspace_filter
        + """))
        """,
        "DELETE FROM become_pilot_transcript_reviews "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM become_voice_transcription_attempts "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM become_voice_correlations "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM become_media_intakes "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM become_relations_consent_handoffs "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM become_controlled_consent_receipts "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM become_controlled_intake_receipts "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM agent_installations "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM agent_deployments "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM instruction_revisions "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM agent_definitions "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM dialogs "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM transport_connectors "
        "WHERE workspace_id IN (" + workspace_filter + ")",
        "DELETE FROM workspaces "
        "WHERE key LIKE 'pilot-postgres-%'",
    )
    async with sessions.begin() as session:
        for statement in statements:
            await session.execute(text(statement))


@pytest.fixture(autouse=True)
async def clean_previous_pilot_fixtures(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    await _clean_pilot_fixtures(sessions)
    try:
        yield
    finally:
        await _clean_pilot_fixtures(sessions)


async def seed_pilot(
    sessions: async_sessionmaker[AsyncSession],
    *,
    attempt_state: str = "confirmed",
    with_review: bool = True,
    staged_expiry: datetime | None = None,
    subject_ref: str = SUBJECT,
    provider_dialog_id: str = CHAT,
) -> PilotGraph:
    workspace_id = uuid4()
    agent_definition_id = uuid4()
    instruction_revision_id = uuid4()
    deployment_id = uuid4()
    connector_id = uuid4()
    dialog_id = uuid4()
    installation_id = uuid4()
    registration_source_id = uuid4()
    consent_source_id = uuid4()
    media_source_id = uuid4()
    registration_id = uuid4()
    consent_id = uuid4()
    handoff_id = uuid4()
    media_id = uuid4()
    correlation_id = uuid4()
    attempt_id = uuid4()
    review_id = uuid4() if with_review else None
    version_id = uuid4()
    delivery_id = uuid4() if with_review else None
    callback_ref = uuid4().hex if with_review else None
    workspace_key = f"pilot-postgres-{workspace_id}"
    installation_ref = f"ref://pilot/installation/{installation_id}"
    correlation_ref = f"synthetic://correlation/voice/{correlation_id.hex}"
    expiry = staged_expiry or NOW + timedelta(days=30)
    provider_success_at = (
        NOW - timedelta(days=31)
        if expiry <= NOW
        else NOW
    )
    staged = "provider private transcript" if attempt_state == "succeeded_unsettled" else None
    provider_started = (
        None
        if attempt_state in {"reserving", "reserved", "blocked"}
        else provider_success_at - timedelta(minutes=2)
    )
    success_state = attempt_state in {
        "succeeded_unsettled",
        "confirmed",
        "reconciliation_required",
    }
    reason = (
        "become_transcription_staged_plaintext_expired"
        if attempt_state == "reconciliation_required"
        else None
    )

    async with sessions.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO workspaces (id, key, display_name, is_active)
                VALUES (:id, :key, 'Pilot PostgreSQL fixture', true)
                """
            ),
            {"id": workspace_id, "key": workspace_key},
        )
        await session.execute(
            text(
                """
                INSERT INTO agent_definitions (
                    id, workspace_id, agent_key, display_name
                ) VALUES (
                    :id, :workspace_id, 'alpha_group', 'Pilot fixture'
                )
                """
            ),
            {"id": agent_definition_id, "workspace_id": workspace_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO instruction_revisions (
                    id, workspace_id, agent_definition_id, revision_key,
                    source_repository, source_path, source_commit_sha,
                    content_sha256, schema_version, validation_state
                ) VALUES (
                    :id, :workspace_id, :agent_id, :key,
                    'fixture/repository', 'fixture/path', :commit_sha,
                    :content_sha, '1', 'validated'
                )
                """
            ),
            {
                "id": instruction_revision_id,
                "workspace_id": workspace_id,
                "agent_id": agent_definition_id,
                "key": f"pilot-revision-{instruction_revision_id}",
                "commit_sha": "a" * 40,
                "content_sha": "b" * 64,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO agent_deployments (
                    id, workspace_id, agent_definition_id,
                    instruction_revision_id, deployment_key,
                    runtime_profile, model_profile, policy_version, state
                ) VALUES (
                    :id, :workspace_id, :agent_id, :revision_id, :key,
                    'pilot-runtime', 'pilot-model', 'pilot-policy-v1', 'active'
                )
                """
            ),
            {
                "id": deployment_id,
                "workspace_id": workspace_id,
                "agent_id": agent_definition_id,
                "revision_id": instruction_revision_id,
                "key": f"pilot-deployment-{deployment_id}",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO transport_connectors (
                    id, workspace_id, connector_key, connector_kind, is_enabled
                ) VALUES (
                    :id, :workspace_id, 'telegram_become_group',
                    'telegram_bot', true
                )
                """
            ),
            {"id": connector_id, "workspace_id": workspace_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO dialogs (
                    id, workspace_id, connector_id,
                    provider_dialog_id, dialog_kind
                ) VALUES (
                    :id, :workspace_id, :connector_id, :dialog, 'group'
                )
                """
            ),
            {
                "id": dialog_id,
                "workspace_id": workspace_id,
                "connector_id": connector_id,
                "dialog": provider_dialog_id,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO agent_installations (
                    id, workspace_id, connector_id, dialog_id,
                    deployment_id, instruction_revision_id,
                    installation_key, dialog_label, state
                ) VALUES (
                    :id, :workspace_id, :connector_id, :dialog_id,
                    :deployment_id, :revision_id,
                    :key, 'Pilot fixture', 'active'
                )
                """
            ),
            {
                "id": installation_id,
                "workspace_id": workspace_id,
                "connector_id": connector_id,
                "dialog_id": dialog_id,
                "deployment_id": deployment_id,
                "revision_id": instruction_revision_id,
                "key": f"installation-{installation_id}",
            },
        )
        for source_id, update_id, payload, processed in (
            (
                registration_source_id,
                registration_source_id.int % 9_000_000_000_000_000_000,
                {"command": "registration"},
                True,
            ),
            (
                consent_source_id,
                consent_source_id.int % 9_000_000_000_000_000_000,
                {"command": "consent"},
                True,
            ),
            (
                media_source_id,
                media_source_id.int % 9_000_000_000_000_000_000,
                {"message": {"voice": {"file_id": "private-file-ref"}}},
                True,
            ),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO telegram_receipts (
                        id, bot_key, update_id, payload, status,
                        received_at, processed_at
                    ) VALUES (
                        :id, 'telegram_become_group', :update_id,
                        CAST(:payload AS jsonb), 'processed',
                        :received_at, :processed_at
                    )
                    """
                ),
                {
                    "id": source_id,
                    "update_id": update_id,
                    "payload": json.dumps(payload),
                    "received_at": NOW - timedelta(days=8),
                    "processed_at": NOW if processed else None,
                },
            )
        await session.execute(
            text(
                """
                UPDATE telegram_receipts
                SET payload = CAST(:payload AS jsonb)
                WHERE id = :source_id
                """
            ),
            {
                "source_id": media_source_id,
                "payload": json.dumps(
                    {
                        "update_id": media_source_id.int
                        % 9_000_000_000_000_000_000,
                        "message": {
                            "message_id": 1,
                            "chat": {
                                "id": provider_dialog_id,
                                "type": "group",
                            },
                            "from": {"id": "1001", "is_bot": False},
                            "voice": {
                                "file_id": "private-file-ref",
                                "file_unique_id": "private-unique-file-ref",
                                "duration": 1,
                                "file_size": 3,
                                "mime_type": "audio/ogg",
                            },
                        },
                    }
                ),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO become_controlled_intake_receipts (
                    id, workspace_id, installation_id, source_receipt_id,
                    receipt_key, receipt_digest, schema_name, connector_key,
                    intent, installation_ref, subject_ref, update_ref,
                    group_state, member_count, payload, received_at
                ) VALUES (
                    :id, :workspace_id, :installation_id, :source_id,
                    :key, :digest, 'gate.become-controlled-intake@1',
                    'telegram_become_group', 'registration_confirmed',
                    :installation_ref, :subject_ref, :update_ref,
                    'owner_only', 2, '{}'::jsonb, :now
                )
                """
            ),
            {
                "id": registration_id,
                "workspace_id": workspace_id,
                "installation_id": installation_id,
                "source_id": registration_source_id,
                "key": f"registration-{registration_id}",
                "digest": "a" * 64,
                "installation_ref": installation_ref,
                "subject_ref": subject_ref,
                "update_ref": f"ref://update/{registration_source_id}",
                "now": NOW,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO become_controlled_consent_receipts (
                    id, workspace_id, installation_id, source_receipt_id,
                    receipt_key, receipt_digest, schema_name, connector_key,
                    intent, installation_ref, quest_ref, correlation_ref,
                    subject_ref, action_ref, policy_version, policy_digest,
                    group_state, member_count, payload, accepted_at
                ) VALUES (
                    :id, :workspace_id, :installation_id, :source_id,
                    :key, :digest,
                    'gate.become-controlled-consent-intake@1',
                    'telegram_become_group', 'consent_accepted',
                    :installation_ref, :quest_ref, :correlation_ref,
                    :subject_ref, :action_ref, 'pilot-policy-v1', :policy_digest,
                    'owner_only', 2, '{}'::jsonb, :now
                )
                """
            ),
            {
                "id": consent_id,
                "workspace_id": workspace_id,
                "installation_id": installation_id,
                "source_id": consent_source_id,
                "key": f"consent-{consent_id}",
                "digest": "b" * 64,
                "installation_ref": installation_ref,
                "quest_ref": f"ref://quest/{workspace_id}",
                "correlation_ref": f"ref://consent/{correlation_id}",
                "subject_ref": subject_ref,
                "action_ref": f"ref://action/{consent_id}",
                "policy_digest": "c" * 64,
                "now": NOW,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO become_relations_consent_handoffs (
                    id, workspace_id, consent_receipt_id, handoff_key,
                    command_digest, schema_name, status, payload,
                    execution_digest, execution_payload, domain_event_id,
                    domain_event_digest, executed_at
                ) VALUES (
                    :id, :workspace_id, :consent_id, :key, :digest,
                    'relations.controlled-consent-command@1', 'executed',
                    '{}'::jsonb, :execution_digest,
                    '{}'::jsonb, :event_id,
                    :event_digest, :now
                )
                """
            ),
            {
                "id": handoff_id,
                "workspace_id": workspace_id,
                "consent_id": consent_id,
                "key": f"handoff-{handoff_id}",
                "digest": "d" * 64,
                "execution_digest": "e" * 64,
                "event_id": f"consent_evt_{handoff_id.hex}",
                "event_digest": "f" * 64,
                "now": NOW,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO become_media_intakes (
                    id, workspace_id, installation_id, source_receipt_id,
                    registration_receipt_id, consent_receipt_id,
                    relations_handoff_id, intake_key, schema_name, subject_ref,
                    update_ref, kind, file_unique_digest, duration_seconds,
                    status, raw_data, raw_sha256, raw_size_bytes,
                    raw_media_type, raw_expires_at, transcript_state,
                    received_at, updated_at
                ) VALUES (
                    :id, :workspace_id, :installation_id, :source_id,
                    :registration_id, :consent_id, :handoff_id, :key,
                    'gate.become-media-intake@1', :subject_ref, :update_ref,
                    'voice', :file_digest, 1, 'archived', :raw_data,
                    :raw_digest, 3, 'audio/ogg', :raw_expiry, 'none',
                    :now, :now
                )
                """
            ),
            {
                "id": media_id,
                "workspace_id": workspace_id,
                "installation_id": installation_id,
                "source_id": media_source_id,
                "registration_id": registration_id,
                "consent_id": consent_id,
                "handoff_id": handoff_id,
                "key": f"media-{media_id}",
                "subject_ref": subject_ref,
                "update_ref": f"ref://update/{media_source_id}",
                "file_digest": "3" * 64,
                "raw_data": b"ogg",
                "raw_digest": "4" * 64,
                "raw_expiry": NOW + timedelta(days=7),
                "now": NOW,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO become_voice_correlations (
                    id, workspace_id, media_intake_id, correlation_ref,
                    schema_name, intake_digest
                ) VALUES (
                    :id, :workspace_id, :media_id, :correlation_ref,
                    'gate.become-voice-correlation@1', :digest
                )
                """
            ),
            {
                "id": correlation_id,
                "workspace_id": workspace_id,
                "media_id": media_id,
                "correlation_ref": correlation_ref,
                "digest": "5" * 64,
            },
        )
        if attempt_state != "absent":
            await session.execute(
                text(
                    """
                    INSERT INTO become_voice_transcription_attempts (
                        id, workspace_id, correlation_id, media_intake_id,
                        grant_key, schema_name, state, reservation_key,
                        period_key, requested_microusd, model, language,
                        provider_call_started_at, provider_operation_ref,
                        staged_transcript, staged_transcript_expires_at,
                        transcript_digest, provider_succeeded_at,
                        billed_quantity, actual_microusd, reason_code,
                        receipt_digest, created_at, updated_at
                    ) VALUES (
                        :id, :workspace_id, :correlation_id, :media_id,
                        'become-pilot-slice1-transcription-v2',
                        'gate.become-voice-transcription@1', :state,
                        :reservation_key, '2026-08', 1000,
                        'gpt-4o-mini-transcribe', 'ru', :provider_started,
                        :provider_ref, :staged, :expiry, :digest,
                        :provider_succeeded, :quantity, :actual, :reason,
                        :receipt_digest, :now, :now
                    )
                    """
                ),
                {
                    "id": attempt_id,
                    "workspace_id": workspace_id,
                    "correlation_id": correlation_id,
                    "media_id": media_id,
                    "state": attempt_state,
                    "reservation_key": f"pilot-reservation-{attempt_id}",
                    "provider_started": provider_started,
                    "provider_ref": "private-provider-ref" if success_state else None,
                    "staged": staged,
                    "expiry": expiry if success_state else None,
                    "digest": DIGEST_1 if success_state else None,
                    "provider_succeeded": (
                        provider_success_at if success_state else None
                    ),
                    "quantity": 10 if success_state else None,
                    "actual": 20 if success_state else None,
                    "reason": reason,
                    "receipt_digest": "6" * 64 if success_state else None,
                    "now": NOW,
                },
            )
        if with_review:
            assert review_id and callback_ref and delivery_id
            await session.execute(
                text(
                    """
                    INSERT INTO become_pilot_transcript_reviews (
                        id, workspace_id, correlation_id, media_intake_id,
                        attempt_id, dialog_id, subject_ref, state,
                        latest_version, callback_ref, created_at, updated_at
                    ) VALUES (
                        :id, :workspace_id, :correlation_id, :media_id,
                        :attempt_id, :dialog_id, :subject_ref, 'pending_display',
                        1, CAST(:callback_ref AS uuid), :now, :now
                    )
                    """
                ),
                {
                    "id": review_id,
                    "workspace_id": workspace_id,
                    "correlation_id": correlation_id,
                    "media_id": media_id,
                    "attempt_id": attempt_id,
                    "dialog_id": dialog_id,
                    "subject_ref": subject_ref,
                    "callback_ref": callback_ref,
                    "now": NOW,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO become_pilot_transcript_versions (
                        id, review_id, version, source, transcript_body,
                        transcript_digest, expires_at, created_at
                    ) VALUES (
                        :id, :review_id, 1, 'provider',
                        'provider private transcript', :digest, :expiry, :now
                    )
                    """
                ),
                {
                    "id": version_id,
                    "review_id": review_id,
                    "digest": DIGEST_1,
                    "expiry": NOW + timedelta(days=30),
                    "now": NOW,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO become_pilot_transcript_deliveries (
                        id, review_id, kind, attempt_no, version,
                        transcript_digest, callback_ref, request_digest,
                        outcome, created_at, updated_at
                    ) VALUES (
                        :id, :review_id, 'presentation', 1, 1, :digest,
                        CAST(:callback_ref AS uuid), :request_digest,
                        'pending', :now, :now
                    )
                    """
                ),
                {
                    "id": delivery_id,
                    "review_id": review_id,
                    "digest": DIGEST_1,
                    "callback_ref": callback_ref,
                    "request_digest": request_digest(
                        kind="presentation",
                        version=1,
                        transcript_digest=DIGEST_1,
                        callback_ref=callback_ref,
                    ),
                    "now": NOW - timedelta(minutes=5),
                },
            )
    return PilotGraph(
        workspace_id=workspace_id,
        workspace_key=workspace_key,
        dialog_id=dialog_id,
        media_id=media_id,
        correlation_id=correlation_id,
        attempt_id=attempt_id,
        review_id=review_id,
        callback_ref=callback_ref,
        delivery_id=delivery_id,
        media_receipt_id=media_source_id,
        subject_ref=subject_ref,
        provider_dialog_id=provider_dialog_id,
    )


def _error_code(exc: pytest.ExceptionInfo[BecomeControlledIntakeError]) -> str:
    return str(exc.value.args[0])


async def review_snapshot(
    sessions: async_sessionmaker[AsyncSession],
    review_id: UUID,
) -> tuple[tuple[object, ...], int, int]:
    async with sessions() as session:
        review = (
            await session.execute(
                text(
                    """
                    SELECT state, latest_version, callback_ref::text,
                           approved_version, approved_digest, approved_at
                    FROM become_pilot_transcript_reviews
                    WHERE id = :review_id
                    """
                ),
                {"review_id": review_id},
            )
        ).one()
        versions = await session.scalar(
            text(
                """
                SELECT count(*) FROM become_pilot_transcript_versions
                WHERE review_id = :review_id
                """
            ),
            {"review_id": review_id},
        )
        deliveries = await session.scalar(
            text(
                """
                SELECT count(*) FROM become_pilot_transcript_deliveries
                WHERE review_id = :review_id
                """
            ),
            {"review_id": review_id},
        )
    return tuple(review), int(versions or 0), int(deliveries or 0)


async def present_initial(
    sessions: async_sessionmaker[AsyncSession],
    graph: PilotGraph,
) -> SessionFactoryPilotTranscriptReviewStore:
    store = SessionFactoryPilotTranscriptReviewStore(sessions)
    job = await store.claim_delivery(now=NOW, max_operations=8)
    assert job is not None
    assert job.delivery_id == str(graph.delivery_id)
    await store.settle_delivery(
        delivery_id=job.delivery_id,
        outcome="sent",
        telegram_message_id="private-presentation",
        now=NOW,
    )
    return store


async def correct_to_version_two(
    sessions: async_sessionmaker[AsyncSession],
    graph: PilotGraph,
) -> tuple[SessionFactoryPilotTranscriptReviewStore, str, str]:
    store = await present_initial(sessions, graph)
    assert graph.callback_ref
    disposition = await store.apply_callback(
        action="e",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW,
    )
    assert disposition is PilotReviewDisposition.CORRECTION_REQUESTED
    prompt = await store.claim_delivery(now=NOW, max_operations=8)
    assert prompt is not None and prompt.kind == "correction_prompt"
    await store.settle_delivery(
        delivery_id=prompt.delivery_id,
        outcome="sent",
        telegram_message_id="private-correction-prompt",
        now=NOW,
    )
    assert await store.apply_correction(
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        correction_text="human corrected transcript",
        now=NOW,
    )
    async with sessions() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT callback_ref::text, transcript_digest
                    FROM become_pilot_transcript_reviews AS r
                    JOIN become_pilot_transcript_versions AS v
                      ON v.review_id = r.id AND v.version = r.latest_version
                    WHERE r.id = :review_id
                    """
                ),
                {"review_id": graph.review_id},
            )
        ).one()
    return store, row[0].replace("-", ""), row[1]


class FakeBudget:
    def __init__(self) -> None:
        self.current: BudgetReservation | None = None
        self.reservations = 0

    async def reserve(self, command: object, *, period_key: str) -> BudgetAdmission:
        del command
        self.reservations += 1
        self.current = BudgetReservation(
            reservation_key="unused-by-store-proof",
            attribution=UsageAttribution(
                workspace_key="fixture",
                product=BillingProduct.GATE,
                scope_kind="become_media_intake",
                scope_ref="telegram_become_group",
            ),
            operation=UsageOperation.TRANSCRIPTION,
            period_key=period_key,
            reserved_microusd=1000,
            state=BudgetReservationState.RESERVED,
            created_at=NOW,
            updated_at=NOW,
        )
        return BudgetAdmission(
            allowed=True,
            reason_code="billing_reserved",
            reservation=self.current,
        )

    async def get_exact(
        self,
        command: object,
        *,
        period_key: str,
    ) -> BudgetReservation | None:
        del command, period_key
        return self.current

    async def settle(self, *_: object, **__: object) -> None:
        assert self.current is not None
        self.current = replace(
            self.current,
            state=BudgetReservationState.SETTLED,
            settled_microusd=20,
            updated_at=NOW,
        )

    async def release(self, *_: object, **__: object) -> None:
        return None

    async def mark_unknown(self, *_: object, **__: object) -> None:
        return None


class FakeNormalizer:
    async def normalize(self, _: object) -> NormalizedVoice:
        return NormalizedVoice(b"normalized", "voice.wav", "audio/wav")


class CountingSpeech:
    ready = True

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, *_: object, **__: object) -> ProviderTranscript:
        self.calls += 1
        await asyncio.sleep(0)
        return ProviderTranscript(
            "provider private transcript",
            "private-provider-operation",
            billed_quantity=10,
            actual_microusd=20,
        )


class NoExternalDownload:
    calls = 0

    async def download(self, **_: object) -> DownloadedBecomeMedia:
        self.calls += 1
        return DownloadedBecomeMedia(b"ogg", "audio/ogg")


class OwnerOnlyPresence:
    async def member_count(self, **_: object) -> int:
        return 2


def media_update(
    graph: PilotGraph,
    *,
    actor_id: str = "1001",
    chat_id: str | None = None,
) -> dict[str, object]:
    return {
        "update_id": graph.media_receipt_id.int
        % 9_000_000_000_000_000_000,
        "message": {
            "message_id": 1,
            "chat": {
                "id": chat_id or graph.provider_dialog_id,
                "type": "group",
            },
            "from": {"id": actor_id, "is_bot": False},
            "voice": {
                "file_id": "private-file-ref",
                "file_unique_id": "private-unique-file-ref",
                "duration": 1,
                "file_size": 3,
                "mime_type": "audio/ogg",
            },
        },
    }


async def remove_seeded_media(
    sessions: async_sessionmaker[AsyncSession], graph: PilotGraph
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            text(
                "DELETE FROM become_voice_correlations WHERE media_intake_id = :id"
            ),
            {"id": graph.media_id},
        )
        await session.execute(
            text("DELETE FROM become_media_intakes WHERE id = :id"),
            {"id": graph.media_id},
        )


def media_service(
    sessions: async_sessionmaker[AsyncSession],
    downloader: NoExternalDownload,
) -> ProcessBecomeMediaService:
    return ProcessBecomeMediaService(
        store=SessionFactoryBecomeMediaStore(sessions),
        downloader=downloader,
        presence=OwnerOnlyPresence(),
        subject_pepper="pilot-pepper",
    )


def provider_service(
    graph: PilotGraph,
    sessions: async_sessionmaker[AsyncSession],
    budget: FakeBudget,
    speech: CountingSpeech,
    *,
    slice1_enabled: bool = True,
    provider_cap: int = 1,
) -> ProcessBecomeVoiceTranscriptionService:
    return ProcessBecomeVoiceTranscriptionService(
        store=cast(
            BecomeVoiceTranscriptionStore,
            SessionFactoryBecomeVoiceTranscriptionStore(sessions),
        ),
        normalizer=cast(BecomeVoiceNormalizer, FakeNormalizer()),
        budget=cast(BecomeTranscriptionBudget, budget),
        speech_to_text=cast(SpeechToTextPort, speech),
        workspace_key=graph.workspace_key,
        model="gpt-4o-mini-transcribe",
        language="ru",
        reserve_microusd_per_second=100,
        enabled=True,
        data_gate_approved=True,
        max_calls=1,
        slice1_enabled=slice1_enabled,
        slice1_provider_max_calls=provider_cap,
    )


async def assert_rejected_media_keeps_provider_zero(
    sessions: async_sessionmaker[AsyncSession],
    *,
    case: str,
) -> None:
    subject_ref = opaque_subject_ref(
        provider_actor_id="1001", pepper="pilot-pepper"
    )
    graph = await seed_pilot(
        sessions,
        attempt_state="absent",
        with_review=False,
        subject_ref=subject_ref,
    )
    await remove_seeded_media(sessions, graph)
    workspace_key = graph.workspace_key
    update = media_update(graph)
    expected_code = "become_media_executed_consent_required"
    if case == "wrong_actor":
        update = media_update(graph, actor_id="1002")
    elif case == "wrong_chat":
        update = media_update(graph, chat_id="wrong-private-chat")
        expected_code = "become_media_installation_invalid"
    elif case == "wrong_workspace":
        workspace_key = graph.workspace_key + "-wrong"
        expected_code = "become_media_installation_invalid"
    elif case == "missing_consent":
        async with sessions.begin() as session:
            await session.execute(
                text(
                    "DELETE FROM become_relations_consent_handoffs "
                    "WHERE workspace_id = :workspace_id"
                ),
                {"workspace_id": graph.workspace_id},
            )
            await session.execute(
                text(
                    "DELETE FROM become_controlled_consent_receipts "
                    "WHERE workspace_id = :workspace_id"
                ),
                {"workspace_id": graph.workspace_id},
            )
    else:
        raise AssertionError(f"unknown identity case: {case}")

    downloader = NoExternalDownload()
    with pytest.raises(BecomeControlledIntakeError) as rejected:
        await media_service(sessions, downloader).execute(
            ProcessBecomeMediaCommand(
                workspace_key=workspace_key,
                source_receipt_id=str(graph.media_receipt_id),
                update=update,
                received_at=NOW,
            )
        )
    assert _error_code(rejected) == expected_code
    speech = CountingSpeech()
    budget = FakeBudget()
    run = await provider_service(
        graph, sessions, budget, speech
    ).run_once(now=NOW)
    assert run.disposition == "idle"
    assert downloader.calls == 0
    assert speech.calls == 0
    assert budget.reservations == 0
    async with sessions() as session:
        provider_rows = await session.scalar(
            text(
                """
                SELECT count(*) FROM become_voice_transcription_attempts
                WHERE workspace_id = :workspace_id
                """
            ),
            {"workspace_id": graph.workspace_id},
        )
        staged_rows = await session.scalar(
            text(
                """
                SELECT count(*) FROM become_voice_transcription_attempts
                WHERE workspace_id = :workspace_id
                  AND staged_transcript IS NOT NULL
                """
            ),
            {"workspace_id": graph.workspace_id},
        )
        version_rows = await session.scalar(
            text(
                """
                SELECT count(*) FROM become_pilot_transcript_reviews
                WHERE workspace_id = :workspace_id
                """
            ),
            {"workspace_id": graph.workspace_id},
        )
        delivery_rows = await session.scalar(
            text(
                """
                SELECT count(*)
                FROM become_pilot_transcript_deliveries AS d
                JOIN become_pilot_transcript_reviews AS r
                  ON r.id = d.review_id
                WHERE r.workspace_id = :workspace_id
                """
            ),
            {"workspace_id": graph.workspace_id},
        )
    assert (provider_rows, staged_rows, version_rows, delivery_rows) == (
        0, 0, 0, 0
    )


@pytest.mark.anyio
async def test_wrong_actor_application_boundary_keeps_provider_calls_zero(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await assert_rejected_media_keeps_provider_zero(
        sessions, case="wrong_actor"
    )


@pytest.mark.anyio
async def test_wrong_chat_application_boundary_keeps_provider_calls_zero(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await assert_rejected_media_keeps_provider_zero(
        sessions, case="wrong_chat"
    )


@pytest.mark.anyio
async def test_wrong_workspace_application_boundary_keeps_provider_calls_zero(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await assert_rejected_media_keeps_provider_zero(
        sessions, case="wrong_workspace"
    )


@pytest.mark.anyio
async def test_missing_consent_application_boundary_keeps_provider_calls_zero(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await assert_rejected_media_keeps_provider_zero(
        sessions, case="missing_consent"
    )


@pytest.mark.anyio
async def test_provider_gate_and_cap_survive_restart_on_postgres(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(
        sessions,
        attempt_state="absent",
        with_review=False,
    )
    speech = CountingSpeech()
    budget = FakeBudget()

    disabled = await provider_service(
        graph,
        sessions,
        budget,
        speech,
        slice1_enabled=False,
        provider_cap=1,
    ).run_once(now=NOW)
    assert disabled.disposition == "disabled"
    assert speech.calls == 0
    async with sessions() as session:
        assert await session.scalar(
            text(
                """
                SELECT count(*) FROM become_voice_transcription_attempts
                WHERE workspace_id = :workspace_id
                """
            ),
            {"workspace_id": graph.workspace_id},
        ) == 0

    first, second = await asyncio.gather(
        provider_service(graph, sessions, budget, speech).run_once(now=NOW),
        provider_service(graph, sessions, budget, speech).run_once(now=NOW),
    )
    assert speech.calls == 1
    assert budget.reservations == 1
    assert {first.disposition, second.disposition} == {
        "settled_pending_human_review",
        "call_not_claimed",
    }

    restarted = await provider_service(
        graph,
        sessions,
        budget,
        speech,
    ).run_once(now=NOW + timedelta(minutes=1))
    assert restarted.disposition == "idle"
    assert speech.calls == 1
    async with sessions() as session:
        attempt_count = await session.scalar(
            text(
                """
                SELECT count(*) FROM become_voice_transcription_attempts
                WHERE workspace_id = :workspace_id
                  AND grant_key = 'become-pilot-slice1-transcription-v2'
                """
            ),
            {"workspace_id": graph.workspace_id},
        )
        approval = (
            await session.execute(
                text(
                    """
                    SELECT state, approved_version
                    FROM become_pilot_transcript_reviews
                    WHERE workspace_id = :workspace_id
                    """
                ),
                {"workspace_id": graph.workspace_id},
            )
        ).one()
    assert attempt_count == 1
    assert approval == ("pending_display", None)


@pytest.mark.anyio
async def test_postgres_identity_append_only_and_exact_latest_approval(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.review_id and graph.callback_ref and graph.delivery_id
    store = SessionFactoryPilotTranscriptReviewStore(sessions)

    with pytest.raises(BecomeControlledIntakeError):
        await store.apply_callback(
            action="c",
            callback_ref=graph.callback_ref,
            subject_ref="ref://pilot/subject/wrong",
            provider_dialog_id=CHAT,
            now=NOW,
        )
    with pytest.raises(BecomeControlledIntakeError):
        await store.apply_callback(
            action="c",
            callback_ref=graph.callback_ref,
            subject_ref=SUBJECT,
            provider_dialog_id="wrong-chat",
            now=NOW,
        )

    job = await store.claim_delivery(now=NOW, max_operations=8)
    assert job is not None
    assert job.kind == "presentation"
    assert job.transcript_source == "provider"
    await store.settle_delivery(
        delivery_id=job.delivery_id,
        outcome="sent",
        telegram_message_id="private-message-1",
        now=NOW,
    )
    requested = await store.apply_callback(
        action="e",
        callback_ref=graph.callback_ref,
        subject_ref=SUBJECT,
        provider_dialog_id=CHAT,
        now=NOW,
    )
    assert requested is PilotReviewDisposition.CORRECTION_REQUESTED
    correction_prompt = await store.claim_delivery(now=NOW, max_operations=8)
    assert correction_prompt is not None
    assert correction_prompt.kind == "correction_prompt"
    await store.settle_delivery(
        delivery_id=correction_prompt.delivery_id,
        outcome="sent",
        telegram_message_id="private-correction-prompt",
        now=NOW,
    )
    assert await store.apply_correction(
        subject_ref="ref://pilot/subject/wrong",
        provider_dialog_id=CHAT,
        correction_text="must not persist",
        now=NOW,
    ) is False
    assert await store.apply_correction(
        subject_ref=SUBJECT,
        provider_dialog_id=CHAT,
        correction_text="human corrected transcript",
        now=NOW,
    ) is True

    async with sessions() as session:
        versions = (
            await session.execute(
                text(
                    """
                    SELECT version, source, transcript_digest
                    FROM become_pilot_transcript_versions
                    WHERE review_id = :review_id ORDER BY version
                    """
                ),
                {"review_id": graph.review_id},
            )
        ).all()
        review = (
            await session.execute(
                text(
                    """
                    SELECT state, latest_version, callback_ref::text,
                           approved_version, approved_digest
                    FROM become_pilot_transcript_reviews
                    WHERE id = :review_id
                    """
                ),
                {"review_id": graph.review_id},
            )
        ).one()
    assert [row[0] for row in versions] == [1, 2]
    assert [row[1] for row in versions] == ["provider", "human_correction"]
    assert review[0:2] == ("pending_display", 2)
    assert review[3:] == (None, None)
    new_callback = review[2].replace("-", "")

    with pytest.raises(BecomeControlledIntakeError):
        await store.apply_callback(
            action="c",
            callback_ref=graph.callback_ref,
            subject_ref=SUBJECT,
            provider_dialog_id=CHAT,
            now=NOW,
        )
    with pytest.raises(BecomeControlledIntakeError):
        await store.owner_redisplay(
            review_id=str(graph.review_id),
            expected_version=1,
            expected_digest=versions[0][2],
            now=NOW,
        )
    with pytest.raises(BecomeControlledIntakeError):
        await store.owner_redisplay(
            review_id=str(graph.review_id),
            expected_version=2,
            expected_digest="f" * 64,
            now=NOW,
        )

    corrected = await store.claim_delivery(now=NOW, max_operations=8)
    assert corrected is not None
    assert corrected.kind == "presentation"
    assert corrected.transcript_source == "human_correction"
    await store.settle_delivery(
        delivery_id=corrected.delivery_id,
        outcome="sent",
        telegram_message_id="private-message-2",
        now=NOW,
    )
    results = await asyncio.gather(
        store.apply_callback(
            action="c",
            callback_ref=new_callback,
            subject_ref=SUBJECT,
            provider_dialog_id=CHAT,
            now=NOW,
        ),
        SessionFactoryPilotTranscriptReviewStore(sessions).apply_callback(
            action="c",
            callback_ref=new_callback,
            subject_ref=SUBJECT,
            provider_dialog_id=CHAT,
            now=NOW,
        ),
    )
    assert set(results) == {
        PilotReviewDisposition.APPROVED,
        PilotReviewDisposition.APPROVED_DUPLICATE,
    }
    duplicate = await store.apply_callback(
        action="c",
        callback_ref=new_callback,
        subject_ref=SUBJECT,
        provider_dialog_id=CHAT,
        now=NOW,
    )
    assert duplicate is PilotReviewDisposition.APPROVED_DUPLICATE
    async with sessions() as session:
        approval = (
            await session.execute(
                text(
                    """
                    SELECT state, latest_version, approved_version,
                           approved_digest
                    FROM become_pilot_transcript_reviews
                    WHERE id = :review_id
                    """
                ),
                {"review_id": graph.review_id},
            )
        ).one()
        confirmations = await session.scalar(
            text(
                """
                SELECT count(*) FROM become_pilot_transcript_deliveries
                WHERE review_id = :review_id AND kind = 'confirmation'
                """
            ),
            {"review_id": graph.review_id},
        )
    assert approval == ("approved", 2, 2, versions[1][2])
    assert confirmations == 1


@pytest.mark.anyio
async def test_cross_pilot_callbacks_fail_closed_without_row_mutation(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    pilot_a = await seed_pilot(
        sessions,
        subject_ref="ref://pilot/subject/a",
        provider_dialog_id="pilot-private-chat-a",
    )
    pilot_b = await seed_pilot(
        sessions,
        subject_ref="ref://pilot/subject/b",
        provider_dialog_id="pilot-private-chat-b",
    )
    assert pilot_a.review_id and pilot_a.callback_ref
    assert pilot_b.review_id and pilot_b.callback_ref
    before_a = await review_snapshot(sessions, pilot_a.review_id)
    before_b = await review_snapshot(sessions, pilot_b.review_id)
    store = SessionFactoryPilotTranscriptReviewStore(sessions)

    with pytest.raises(BecomeControlledIntakeError) as callback_a_from_b:
        await store.apply_callback(
            action="c",
            callback_ref=pilot_a.callback_ref,
            subject_ref=pilot_b.subject_ref,
            provider_dialog_id=pilot_b.provider_dialog_id,
            now=NOW,
        )
    assert _error_code(callback_a_from_b) == (
        "become_pilot_callback_binding_invalid"
    )
    with pytest.raises(BecomeControlledIntakeError) as callback_b_to_a:
        await store.apply_callback(
            action="c",
            callback_ref=pilot_b.callback_ref,
            subject_ref=pilot_a.subject_ref,
            provider_dialog_id=pilot_a.provider_dialog_id,
            now=NOW,
        )
    assert _error_code(callback_b_to_a) == (
        "become_pilot_callback_binding_invalid"
    )
    assert await review_snapshot(sessions, pilot_a.review_id) == before_a
    assert await review_snapshot(sessions, pilot_b.review_id) == before_b


@pytest.mark.anyio
async def test_version_digest_exact_replay_differs_from_mutated_and_lower(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.review_id
    store = await present_initial(sessions, graph)
    assert await store.owner_redisplay(
        review_id=str(graph.review_id),
        expected_version=1,
        expected_digest=DIGEST_1,
        now=NOW,
    ) == "redisplay_scheduled"
    exact_snapshot = await review_snapshot(sessions, graph.review_id)
    assert await store.owner_redisplay(
        review_id=str(graph.review_id),
        expected_version=1,
        expected_digest=DIGEST_1,
        now=NOW,
    ) == "redisplay_scheduled"
    assert await review_snapshot(sessions, graph.review_id) == exact_snapshot

    with pytest.raises(BecomeControlledIntakeError) as mutated:
        await store.owner_redisplay(
            review_id=str(graph.review_id),
            expected_version=1,
            expected_digest="f" * 64,
            now=NOW,
        )
    assert _error_code(mutated) == "become_pilot_operator_binding_invalid"
    with pytest.raises(BecomeControlledIntakeError) as lower:
        await store.owner_redisplay(
            review_id=str(graph.review_id),
            expected_version=0,
            expected_digest=DIGEST_1,
            now=NOW,
        )
    assert _error_code(lower) == "become_pilot_operator_binding_invalid"
    assert await review_snapshot(sessions, graph.review_id) == exact_snapshot


@pytest.mark.anyio
async def test_higher_version_accepts_then_stale_and_wrong_digest_reject(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.review_id and graph.callback_ref
    store, callback_v2, digest_v2 = await correct_to_version_two(
        sessions, graph
    )
    expected = await review_snapshot(sessions, graph.review_id)
    assert expected[0][0:2] == ("pending_display", 2)
    with pytest.raises(BecomeControlledIntakeError) as stale:
        await store.apply_callback(
            action="c",
            callback_ref=graph.callback_ref,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW,
        )
    assert _error_code(stale) == "become_pilot_callback_binding_invalid"

    corrected = await store.claim_delivery(now=NOW, max_operations=8)
    assert corrected is not None and corrected.version == 2
    await store.settle_delivery(
        delivery_id=corrected.delivery_id,
        outcome="sent",
        telegram_message_id="private-version-two",
        now=NOW,
    )
    with pytest.raises(BecomeControlledIntakeError) as wrong_digest:
        await store.owner_approve(
            review_id=str(graph.review_id),
            expected_version=2,
            expected_digest="f" * 64,
            now=NOW,
        )
    assert _error_code(wrong_digest) == "become_pilot_operator_binding_invalid"
    presented = await review_snapshot(sessions, graph.review_id)
    assert presented[0][0:2] == ("presented", 2)
    assert presented[0][3:6] == (None, None, None)
    assert callback_v2 == presented[0][2].replace("-", "")
    assert digest_v2 != DIGEST_1


@pytest.mark.anyio
async def test_approval_v1_is_invalidated_by_correction_v2_and_restart_safe(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.review_id and graph.callback_ref
    store = await present_initial(sessions, graph)
    assert await store.apply_callback(
        action="c",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW,
    ) is PilotReviewDisposition.APPROVED
    confirmation_v1 = await store.claim_delivery(now=NOW, max_operations=8)
    assert confirmation_v1 is not None and confirmation_v1.kind == "confirmation"
    await store.settle_delivery(
        delivery_id=confirmation_v1.delivery_id,
        outcome="sent",
        telegram_message_id="private-confirmation-v1",
        now=NOW,
    )

    restarted = SessionFactoryPilotTranscriptReviewStore(sessions)
    assert await restarted.apply_callback(
        action="e",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW + timedelta(seconds=1),
    ) is PilotReviewDisposition.CORRECTION_REQUESTED
    prompt = await restarted.claim_delivery(
        now=NOW + timedelta(seconds=1), max_operations=8
    )
    assert prompt is not None and prompt.kind == "correction_prompt"
    await restarted.settle_delivery(
        delivery_id=prompt.delivery_id,
        outcome="sent",
        telegram_message_id="private-correction-after-v1",
        now=NOW + timedelta(seconds=1),
    )
    assert await restarted.apply_correction(
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        correction_text="approved transcript corrected once",
        now=NOW + timedelta(seconds=2),
    )
    async with sessions() as session:
        invalidated = (
            await session.execute(
                text(
                    """
                    SELECT r.state, r.latest_version, r.approved_version,
                           r.approved_digest, m.transcript_state,
                           count(dl.id) FILTER (
                             WHERE dl.kind = 'confirmation' AND dl.version = 1
                           )
                    FROM become_pilot_transcript_reviews AS r
                    JOIN become_media_intakes AS m ON m.id = r.media_intake_id
                    LEFT JOIN become_pilot_transcript_deliveries AS dl
                      ON dl.review_id = r.id
                    WHERE r.id = :review_id
                    GROUP BY r.id, m.id
                    """
                ),
                {"review_id": graph.review_id},
            )
        ).one()
    assert invalidated == ("pending_display", 2, None, None, "none", 1)

    with pytest.raises(BecomeControlledIntakeError):
        await restarted.apply_callback(
            action="c",
            callback_ref=graph.callback_ref,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW + timedelta(seconds=2),
        )
    version_two = await restarted.claim_delivery(
        now=NOW + timedelta(seconds=2), max_operations=8
    )
    assert version_two is not None and version_two.version == 2
    await restarted.settle_delivery(
        delivery_id=version_two.delivery_id,
        outcome="sent",
        telegram_message_id="private-version-two-after-approval",
        now=NOW + timedelta(seconds=2),
    )
    disposition = await restarted.apply_callback(
        action="c",
        callback_ref=version_two.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW + timedelta(seconds=3),
    )
    assert disposition is PilotReviewDisposition.APPROVED
    after_restart = SessionFactoryPilotTranscriptReviewStore(sessions)
    assert await after_restart.apply_callback(
        action="c",
        callback_ref=version_two.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW + timedelta(seconds=4),
    ) is PilotReviewDisposition.APPROVED_DUPLICATE
    final = await review_snapshot(sessions, graph.review_id)
    assert final[0][0:2] == ("approved", 2)
    assert final[0][3] == 2


@pytest.mark.anyio
async def test_restart_after_voice_intake_replays_one_persisted_intake(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    subject_ref = opaque_subject_ref(
        provider_actor_id="1001", pepper="pilot-pepper"
    )
    graph = await seed_pilot(
        sessions,
        attempt_state="absent",
        with_review=False,
        subject_ref=subject_ref,
    )
    await remove_seeded_media(sessions, graph)
    downloader = NoExternalDownload()
    command = ProcessBecomeMediaCommand(
        workspace_key=graph.workspace_key,
        source_receipt_id=str(graph.media_receipt_id),
        update=media_update(graph),
        received_at=NOW,
    )
    first = await media_service(sessions, downloader).execute(command)
    assert first.archived is True and first.duplicate is False
    exact_replay = await media_service(sessions, downloader).execute(command)
    assert exact_replay.archived is True and exact_replay.duplicate is True
    assert exact_replay.intake_key == first.intake_key
    assert downloader.calls == 1
    mutated = media_update(graph)
    message = cast(dict[str, object], mutated["message"])
    voice = cast(dict[str, object], message["voice"])
    voice["file_unique_id"] = "mutated-private-unique-file"
    with pytest.raises(BecomeControlledIntakeError):
        await media_service(sessions, downloader).execute(
            replace(command, update=mutated)
        )
    async with sessions() as session:
        assert await session.scalar(
            text(
                """
                SELECT count(*) FROM become_media_intakes
                WHERE workspace_id = :workspace_id
                """
            ),
            {"workspace_id": graph.workspace_id},
        ) == 1
        assert await session.scalar(
            text(
                """
                SELECT count(*) FROM become_voice_transcription_attempts
                WHERE workspace_id = :workspace_id
                """
            ),
            {"workspace_id": graph.workspace_id},
        ) == 0


@pytest.mark.anyio
async def test_restart_after_provider_reservation_preserves_single_call_claim(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions, attempt_state="absent", with_review=False)
    first_store = SessionFactoryBecomeVoiceTranscriptionStore(sessions)
    work = await first_store.claim_or_resume(
        workspace_key=graph.workspace_key,
        now=NOW,
        model="gpt-4o-mini-transcribe",
        language="ru",
        reserve_microusd_per_second=100,
        stale_after=timedelta(minutes=5),
    )
    assert work is not None
    reserved = await first_store.mark_reserved(
        attempt_id=work.attempt_id, now=NOW
    )
    assert reserved.state.value == "reserved"
    assert (await first_store.mark_reserved(
        attempt_id=work.attempt_id, now=NOW
    )).attempt_id == work.attempt_id

    restarted = SessionFactoryBecomeVoiceTranscriptionStore(sessions)
    calling = await restarted.begin_call(attempt_id=work.attempt_id, now=NOW)
    assert calling is not None and calling.state.value == "calling"
    assert await SessionFactoryBecomeVoiceTranscriptionStore(
        sessions
    ).begin_call(attempt_id=work.attempt_id, now=NOW) is None
    with pytest.raises(BecomeControlledIntakeError):
        await restarted.mark_reserved(attempt_id=str(uuid4()), now=NOW)


@pytest.mark.anyio
async def test_restart_after_provider_success_stages_exactly_one_review(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions, attempt_state="absent", with_review=False)
    first_store = SessionFactoryBecomeVoiceTranscriptionStore(sessions)
    work = await first_store.claim_or_resume(
        workspace_key=graph.workspace_key,
        now=NOW,
        model="gpt-4o-mini-transcribe",
        language="ru",
        reserve_microusd_per_second=100,
        stale_after=timedelta(minutes=5),
    )
    assert work is not None
    await first_store.mark_reserved(attempt_id=work.attempt_id, now=NOW)
    assert await first_store.begin_call(
        attempt_id=work.attempt_id, now=NOW
    ) is not None
    succeeded = await first_store.persist_success(
        attempt_id=work.attempt_id,
        transcript=ProviderTranscript(
            "provider private transcript",
            "private-provider-operation",
            billed_quantity=10,
            actual_microusd=20,
        ),
        confirmed_at=NOW,
    )
    assert succeeded.state.value == "succeeded_unsettled"
    restarted = SessionFactoryBecomeVoiceTranscriptionStore(sessions)
    assert await restarted.confirm_success(
        attempt_id=work.attempt_id, now=NOW
    ) is True
    assert await SessionFactoryBecomeVoiceTranscriptionStore(
        sessions
    ).confirm_success(attempt_id=work.attempt_id, now=NOW) is True
    with pytest.raises(BecomeControlledIntakeError):
        await restarted.confirm_success(attempt_id=str(uuid4()), now=NOW)
    async with sessions() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT a.state, a.staged_transcript,
                           count(r.id), count(d.id)
                    FROM become_voice_transcription_attempts AS a
                    LEFT JOIN become_pilot_transcript_reviews AS r
                      ON r.attempt_id = a.id
                    LEFT JOIN become_pilot_transcript_deliveries AS d
                      ON d.review_id = r.id
                    WHERE a.id = :attempt_id
                    GROUP BY a.id
                    """
                ),
                {"attempt_id": work.attempt_id},
            )
        ).one()
    assert row == ("confirmed", None, 1, 1)


@pytest.mark.anyio
async def test_restart_after_transcript_version_persistence_keeps_latest_digest(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.review_id
    before = await review_snapshot(sessions, graph.review_id)
    restarted = SessionFactoryPilotTranscriptReviewStore(sessions)
    job = await restarted.claim_delivery(now=NOW, max_operations=8)
    assert job is not None
    assert job.version == 1 and job.transcript_digest == DIGEST_1
    after = await review_snapshot(sessions, graph.review_id)
    assert after[0][0:2] == before[0][0:2]
    assert after[1:] == before[1:]
    assert await SessionFactoryPilotTranscriptReviewStore(
        sessions
    ).claim_delivery(now=NOW, max_operations=8) is None


@pytest.mark.anyio
async def test_restart_after_delivery_reservation_never_automatically_resends(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.delivery_id
    first = SessionFactoryPilotTranscriptReviewStore(sessions)
    reserved = await first.claim_delivery(now=NOW, max_operations=8)
    assert reserved is not None
    restarted = SessionFactoryPilotTranscriptReviewStore(sessions)
    assert await restarted.claim_delivery(now=NOW, max_operations=8) is None
    status = await restarted.status(now=NOW)
    assert status["delivery_reserved"] == 1
    await restarted.settle_delivery(
        delivery_id=reserved.delivery_id,
        outcome="unknown",
        telegram_message_id=None,
        now=NOW,
    )
    assert await restarted.claim_delivery(now=NOW, max_operations=8) is None
    with pytest.raises(BecomeControlledIntakeError) as mutated:
        await restarted.settle_delivery(
            delivery_id=reserved.delivery_id,
            outcome="failed",
            telegram_message_id=None,
            now=NOW,
        )
    assert _error_code(mutated) == "become_pilot_delivery_replay_invalid"


@pytest.mark.anyio
async def test_restart_after_delivery_success_continues_with_one_edit_prompt(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.callback_ref and graph.review_id
    await present_initial(sessions, graph)
    restarted = SessionFactoryPilotTranscriptReviewStore(sessions)
    first = await restarted.apply_callback(
        action="e",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW,
    )
    replay = await SessionFactoryPilotTranscriptReviewStore(
        sessions
    ).apply_callback(
        action="e",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW,
    )
    assert first is replay is PilotReviewDisposition.CORRECTION_REQUESTED
    async with sessions() as session:
        prompts = await session.scalar(
            text(
                """
                SELECT count(*) FROM become_pilot_transcript_deliveries
                WHERE review_id = :review_id AND kind = 'correction_prompt'
                """
            ),
            {"review_id": graph.review_id},
        )
    assert prompts == 1


@pytest.mark.anyio
async def test_restart_after_correction_state_accepts_only_bound_actor_text(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.callback_ref and graph.review_id
    store = await present_initial(sessions, graph)
    await store.apply_callback(
        action="e",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW,
    )
    restarted = SessionFactoryPilotTranscriptReviewStore(sessions)
    assert await restarted.apply_correction(
        subject_ref="ref://pilot/subject/mutated",
        provider_dialog_id=graph.provider_dialog_id,
        correction_text="must not persist",
        now=NOW,
    ) is False
    assert await restarted.apply_correction(
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        correction_text="restart correction",
        now=NOW,
    ) is True
    assert await restarted.apply_correction(
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        correction_text="duplicate must not append",
        now=NOW,
    ) is False
    snapshot = await review_snapshot(sessions, graph.review_id)
    assert snapshot[0][0:2] == ("pending_display", 2)
    assert snapshot[1] == 2


@pytest.mark.anyio
async def test_restart_after_corrected_version_rejects_stale_then_approves_latest(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.callback_ref and graph.review_id
    _, callback_v2, digest_v2 = await correct_to_version_two(sessions, graph)
    restarted = SessionFactoryPilotTranscriptReviewStore(sessions)
    with pytest.raises(BecomeControlledIntakeError):
        await restarted.apply_callback(
            action="c",
            callback_ref=graph.callback_ref,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW,
        )
    job = await restarted.claim_delivery(now=NOW, max_operations=8)
    assert job is not None and job.version == 2
    await restarted.settle_delivery(
        delivery_id=job.delivery_id,
        outcome="sent",
        telegram_message_id="private-corrected-version",
        now=NOW,
    )
    with pytest.raises(BecomeControlledIntakeError):
        await restarted.owner_approve(
            review_id=str(graph.review_id),
            expected_version=2,
            expected_digest="f" * 64,
            now=NOW,
        )
    assert await restarted.apply_callback(
        action="c",
        callback_ref=callback_v2,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW,
    ) is PilotReviewDisposition.APPROVED
    snapshot = await review_snapshot(sessions, graph.review_id)
    assert snapshot[0][0:2] == ("approved", 2)
    assert snapshot[0][3:5] == (2, digest_v2)


@pytest.mark.anyio
async def test_restart_after_human_approval_is_durable_exact_no_op(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.callback_ref and graph.review_id
    store = await present_initial(sessions, graph)
    assert await store.apply_callback(
        action="c",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW,
    ) is PilotReviewDisposition.APPROVED
    restarted = SessionFactoryPilotTranscriptReviewStore(sessions)
    assert await restarted.apply_callback(
        action="c",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW,
    ) is PilotReviewDisposition.APPROVED_DUPLICATE
    with pytest.raises(BecomeControlledIntakeError) as mutated:
        await restarted.apply_callback(
            action="c",
            callback_ref=uuid4().hex,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW,
        )
    assert _error_code(mutated) == "become_pilot_callback_binding_invalid"
    snapshot = await review_snapshot(sessions, graph.review_id)
    assert snapshot[0][0:5] == ("approved", 1, snapshot[0][2], 1, DIGEST_1)
    assert snapshot[1] == 1


@pytest.mark.anyio
async def test_delivery_crash_visibility_and_bounded_owner_reconciliation(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(sessions)
    assert graph.delivery_id
    store = SessionFactoryPilotTranscriptReviewStore(sessions)

    first, second = await asyncio.gather(
        store.claim_delivery(now=NOW, max_operations=8),
        SessionFactoryPilotTranscriptReviewStore(sessions).claim_delivery(
            now=NOW,
            max_operations=8,
        ),
    )
    assert sum(job is not None for job in (first, second)) == 1
    assert await store.claim_delivery(now=NOW, max_operations=8) is None

    status = await store.status(now=NOW)
    assert status["delivery_reserved"] == 1
    assert status["delivery_reserved_oldest_seconds"] >= 300
    assert status["delivery_owner_reconciliation_required"] == 1

    with pytest.raises(BecomeControlledIntakeError):
        await store.owner_reconcile_delivery(
            delivery_id=str(graph.delivery_id),
            action="mark_failed",
            evidence_digest=EVIDENCE,
            external_message_id=None,
            owner_authorized=False,
            now=NOW,
        )
    with pytest.raises(BecomeControlledIntakeError):
        await store.owner_reconcile_delivery(
            delivery_id=str(uuid4()),
            action="mark_failed",
            evidence_digest=EVIDENCE,
            external_message_id=None,
            owner_authorized=True,
            now=NOW,
        )
    assert await store.owner_reconcile_delivery(
        delivery_id=str(graph.delivery_id),
        action="mark_failed",
        evidence_digest=EVIDENCE,
        external_message_id=None,
        owner_authorized=True,
        now=NOW,
    ) == "failed_confirmed"
    assert await store.owner_reconcile_delivery(
        delivery_id=str(graph.delivery_id),
        action="mark_failed",
        evidence_digest=EVIDENCE,
        external_message_id=None,
        owner_authorized=True,
        now=NOW,
    ) == "reconciliation_duplicate"
    failed_status = await store.status(now=NOW)
    assert failed_status["delivery_failed"] == 1
    assert failed_status["delivery_failed_oldest_seconds"] >= 300
    assert failed_status["delivery_owner_reconciliation_required"] == 1

    assert await store.owner_reconcile_delivery(
        delivery_id=str(graph.delivery_id),
        action="authorize_retry",
        evidence_digest="a" * 64,
        external_message_id=None,
        owner_authorized=True,
        now=NOW,
    ) == "retry_authorized"
    assert await store.owner_reconcile_delivery(
        delivery_id=str(graph.delivery_id),
        action="authorize_retry",
        evidence_digest="a" * 64,
        external_message_id=None,
        owner_authorized=True,
        now=NOW,
    ) == "reconciliation_duplicate"
    async with sessions() as session:
        attempts = (
            await session.execute(
                text(
                    """
                    SELECT attempt_no, outcome, retry_of_delivery_id::text
                    FROM become_pilot_transcript_deliveries
                    WHERE review_id = :review_id ORDER BY attempt_no
                    """
                ),
                {"review_id": graph.review_id},
            )
        ).all()
        audit_count = await session.scalar(
            text(
                """
                SELECT count(*)
                FROM become_pilot_delivery_reconciliations
                WHERE delivery_id = :delivery_id
                """
            ),
            {"delivery_id": graph.delivery_id},
        )
    assert attempts[0] == (1, "failed", None)
    assert attempts[1][0:2] == (2, "pending")
    assert attempts[1][2] == str(graph.delivery_id)
    assert audit_count == 2

    retry = await store.claim_delivery(now=NOW, max_operations=8)
    assert retry is not None
    await store.settle_delivery(
        delivery_id=retry.delivery_id,
        outcome="unknown",
        telegram_message_id=None,
        now=NOW,
    )
    assert await store.claim_delivery(now=NOW, max_operations=8) is None
    unknown_status = await store.status(now=NOW + timedelta(minutes=5))
    assert unknown_status["delivery_unknown"] == 1
    assert unknown_status["delivery_unknown_oldest_seconds"] >= 300
    assert unknown_status["delivery_owner_reconciliation_required"] >= 1
    assert await store.owner_reconcile_delivery(
        delivery_id=retry.delivery_id,
        action="confirm_sent",
        evidence_digest="b" * 64,
        external_message_id="external-proof-message",
        owner_authorized=True,
        now=NOW + timedelta(minutes=5),
    ) == "sent_confirmed"
    assert await store.owner_reconcile_delivery(
        delivery_id=retry.delivery_id,
        action="confirm_sent",
        evidence_digest="b" * 64,
        external_message_id="external-proof-message",
        owner_authorized=True,
        now=NOW + timedelta(minutes=5),
    ) == "reconciliation_duplicate"
    with pytest.raises(BecomeControlledIntakeError):
        await store.owner_reconcile_delivery(
            delivery_id=retry.delivery_id,
            action="confirm_sent",
            evidence_digest="c" * 64,
            external_message_id="external-proof-message",
            owner_authorized=True,
            now=NOW + timedelta(minutes=5),
        )
    delivered_status = await store.status(now=NOW + timedelta(minutes=5))
    assert delivered_status["delivery_delivered"] == 1
    assert delivered_status["delivery_unknown"] == 0
    assert await store.claim_delivery(now=NOW, max_operations=8) is None
    with pytest.raises(BecomeControlledIntakeError):
        await store.owner_reconcile_delivery(
            delivery_id=retry.delivery_id,
            action="authorize_retry",
            evidence_digest="d" * 64,
            external_message_id=None,
            owner_authorized=True,
            now=NOW + timedelta(minutes=5),
        )


@pytest.mark.anyio
async def test_telegram_update_replay_survives_restart_and_rejects_mutation(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    update_id = uuid4().int % 9_000_000_000_000_000_000
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "chat": {"id": "private-pilot", "type": "group"},
            "from": {"id": "exact-expert", "is_bot": False},
            "voice": {
                "file_id": "private-file",
                "file_unique_id": "private-unique-file",
                "duration": 1,
            },
        },
    }
    command = AcceptTelegramReceiptCommand(
        bot_key="telegram_become_group",
        update_id=update_id,
        payload=payload,
        received_at=NOW,
    )
    async with sessions() as first_session:
        service = AcceptTelegramReceiptService(
            SqlTelegramReceiptStore(first_session)
        )
        first = await service.execute(command)
        before_restart = await service.execute(command)
    assert first.duplicate is False
    assert before_restart.duplicate is True
    assert before_restart.receipt_id == first.receipt_id
    assert before_restart.outbox_event_id == first.outbox_event_id

    async with sessions() as restarted_session:
        restarted = AcceptTelegramReceiptService(
            SqlTelegramReceiptStore(restarted_session)
        )
        after_restart = await restarted.execute(command)
        assert after_restart.duplicate is True
        assert after_restart.receipt_id == first.receipt_id
        with pytest.raises(TelegramReceiptError) as mutated:
            await restarted.execute(
                replace(
                    command,
                    payload={
                        **payload,
                        "message": {
                            **payload["message"],
                            "message_id": 2,
                        },
                    },
                )
            )
    assert str(mutated.value) == "telegram_receipt_mutated_replay"

    async with sessions.begin() as session:
        receipt_count = await session.scalar(
            text(
                """
                SELECT count(*) FROM telegram_receipts
                WHERE bot_key = 'telegram_become_group'
                  AND update_id = :update_id
                """
            ),
            {"update_id": update_id},
        )
        outbox_count = await session.scalar(
            text(
                """
                SELECT count(*) FROM telegram_outbox_events
                WHERE receipt_id = CAST(:receipt_id AS uuid)
                """
            ),
            {"receipt_id": first.receipt_id},
        )
        provider_count = await session.scalar(
            text("SELECT count(*) FROM become_voice_transcription_attempts")
        )
        delivery_count = await session.scalar(
            text("SELECT count(*) FROM become_pilot_transcript_deliveries")
        )
        await session.execute(
            text(
                """
                DELETE FROM telegram_outbox_events
                WHERE receipt_id = CAST(:receipt_id AS uuid)
                """
            ),
            {"receipt_id": first.receipt_id},
        )
        await session.execute(
            text(
                """
                DELETE FROM telegram_receipts
                WHERE id = CAST(:receipt_id AS uuid)
                """
            ),
            {"receipt_id": first.receipt_id},
        )
    assert receipt_count == 1
    assert outbox_count == 1
    assert provider_count == 0
    assert delivery_count == 0


@pytest.mark.anyio
async def test_telegram_tombstone_replay_survives_restart_and_rejects_mutation(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    update_id = uuid4().int % 9_000_000_000_000_000_000
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 17,
            "chat": {"id": "private-pilot", "type": "group"},
            "from": {"id": "exact-expert", "is_bot": False},
            "voice": {
                "file_id": "private-file",
                "file_unique_id": "private-unique-file",
                "duration": 1,
            },
        },
    }
    command = AcceptTelegramReceiptCommand(
        bot_key="telegram_become_group",
        update_id=update_id,
        payload=payload,
        received_at=NOW,
    )
    original_digest = canonical_telegram_payload_digest(payload)
    external_effects = {
        "provider_calls": 0,
        "telegram_deliveries": 0,
        "downstream_posts": 0,
        "payments": 0,
    }

    async def persisted_effect_state() -> tuple[object, ...]:
        async with sessions() as effect_session:
            return (
                await effect_session.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*)
                           FROM become_voice_transcription_attempts),
                          (SELECT count(*)
                           FROM become_pilot_transcript_deliveries),
                          (SELECT count(*)
                           FROM become_pilot_transcript_versions),
                          (SELECT count(*)
                           FROM become_pilot_transcript_reviews
                           WHERE approved_version IS NOT NULL)
                        """
                    )
                )
            ).one()

    effects_before = await persisted_effect_state()

    class ContentFreeProcessor:
        async def process(self, item: object) -> None:
            assert item.payload == payload

    async with sessions() as first_session:
        first = await AcceptTelegramReceiptService(
            SqlTelegramReceiptStore(first_session)
        ).execute(command)
    assert first.duplicate is False

    async with sessions() as worker_session:
        worker_result = await ProcessTelegramOutboxService(
            queue=SqlTelegramProcessingQueue(worker_session),
            processor=ContentFreeProcessor(),
        ).run_once(now=NOW + timedelta(seconds=1))
    assert worker_result.disposition == TelegramWorkerDisposition.PROCESSED
    assert worker_result.receipt_id == first.receipt_id

    review_store = SessionFactoryPilotTranscriptReviewStore(sessions)
    await review_store.tombstone_receipt(
        receipt_id=first.receipt_id,
        now=NOW + timedelta(seconds=2),
    )
    async with sessions() as tombstoned_session:
        tombstone = (
            await tombstoned_session.execute(
                text(
                    """
                    SELECT payload FROM telegram_receipts
                    WHERE id = CAST(:receipt_id AS uuid)
                    """
                ),
                {"receipt_id": first.receipt_id},
            )
        ).scalar_one()
    assert tombstone["canonical_payload_digest"] == original_digest
    assert tombstone["canonicalization_version"] == "telegram-json-c14n-v1"
    assert tombstone["content_purged"] is True
    assert "message" not in tombstone
    assert "private-file" not in json.dumps(tombstone)

    async def durable_snapshot() -> tuple[object, ...]:
        async with sessions() as snapshot_session:
            return (
                await snapshot_session.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM telegram_receipts
                           WHERE bot_key = 'telegram_become_group'
                             AND update_id = :update_id),
                          (SELECT count(*) FROM telegram_outbox_events
                           WHERE receipt_id = CAST(:receipt_id AS uuid)),
                          (SELECT status FROM telegram_receipts
                           WHERE id = CAST(:receipt_id AS uuid)),
                          (SELECT status FROM telegram_outbox_events
                           WHERE receipt_id = CAST(:receipt_id AS uuid)),
                          (SELECT count(*)
                           FROM become_voice_transcription_attempts),
                          (SELECT count(*)
                           FROM become_pilot_transcript_deliveries),
                          (SELECT count(*)
                           FROM become_pilot_transcript_versions),
                          (SELECT count(*)
                           FROM become_pilot_transcript_reviews
                           WHERE approved_version IS NOT NULL)
                        """
                    ),
                    {
                        "update_id": update_id,
                        "receipt_id": first.receipt_id,
                    },
                )
            ).one()

    before_replay = await durable_snapshot()
    async with sessions() as restarted_session:
        restarted = AcceptTelegramReceiptService(
            SqlTelegramReceiptStore(restarted_session)
        )
        exact_replay = await restarted.execute(command)
    assert exact_replay.duplicate is True
    assert exact_replay.receipt_id == first.receipt_id
    assert exact_replay.outbox_event_id == first.outbox_event_id
    after_exact_replay = await durable_snapshot()
    assert after_exact_replay == before_replay

    async with sessions() as mutated_session:
        restarted = AcceptTelegramReceiptService(
            SqlTelegramReceiptStore(mutated_session)
        )
        with pytest.raises(TelegramReceiptError) as mutated:
            await restarted.execute(
                replace(
                    command,
                    payload={
                        **payload,
                        "message": {**payload["message"], "message_id": 18},
                    },
                )
            )
    assert str(mutated.value) == "telegram_receipt_mutated_replay"
    assert await durable_snapshot() == before_replay
    assert before_replay[0:4] == (1, 1, "processed", "delivered")
    assert await persisted_effect_state() == effects_before
    assert external_effects == {
        "provider_calls": 0,
        "telegram_deliveries": 0,
        "downstream_posts": 0,
        "payments": 0,
    }

    async with sessions.begin() as cleanup_session:
        await cleanup_session.execute(
            text(
                "DELETE FROM telegram_outbox_events "
                "WHERE receipt_id = CAST(:receipt_id AS uuid)"
            ),
            {"receipt_id": first.receipt_id},
        )
        await cleanup_session.execute(
            text(
                "DELETE FROM telegram_receipts "
                "WHERE id = CAST(:receipt_id AS uuid)"
            ),
            {"receipt_id": first.receipt_id},
        )


@pytest.mark.anyio
async def test_legacy_telegram_tombstone_without_digest_fails_closed_after_restart(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    update_id = uuid4().int % 9_000_000_000_000_000_000
    command = AcceptTelegramReceiptCommand(
        bot_key="telegram_become_group",
        update_id=update_id,
        payload={"update_id": update_id, "message": {"text": "private"}},
        received_at=NOW,
    )
    async with sessions() as first_session:
        first = await AcceptTelegramReceiptService(
            SqlTelegramReceiptStore(first_session)
        ).execute(command)
    async with sessions.begin() as tombstone_session:
        await tombstone_session.execute(
            text(
                """
                UPDATE telegram_receipts
                SET payload = CAST(:legacy_tombstone AS jsonb)
                WHERE id = CAST(:receipt_id AS uuid)
                """
            ),
            {
                "receipt_id": first.receipt_id,
                "legacy_tombstone": json.dumps(
                    {
                        "schema": "gate.telegram-content-tombstone@1",
                        "update_id": update_id,
                        "content_purged": True,
                    }
                ),
            },
        )

    async with sessions() as restarted_session:
        restarted = AcceptTelegramReceiptService(
            SqlTelegramReceiptStore(restarted_session)
        )
        with pytest.raises(TelegramReceiptError) as replay:
            await restarted.execute(command)
    assert str(replay.value) == "telegram_receipt_original_digest_missing"

    async with sessions.begin() as cleanup_session:
        state = (
            await cleanup_session.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM telegram_receipts
                       WHERE id = CAST(:receipt_id AS uuid)),
                      (SELECT count(*) FROM telegram_outbox_events
                       WHERE receipt_id = CAST(:receipt_id AS uuid)),
                      (SELECT payload FROM telegram_receipts
                       WHERE id = CAST(:receipt_id AS uuid))
                    """
                ),
                {"receipt_id": first.receipt_id},
            )
        ).one()
        assert state[0:2] == (1, 1)
        assert state[2]["content_purged"] is True
        assert "message" not in state[2]
        await cleanup_session.execute(
            text(
                "DELETE FROM telegram_outbox_events "
                "WHERE receipt_id = CAST(:receipt_id AS uuid)"
            ),
            {"receipt_id": first.receipt_id},
        )
        await cleanup_session.execute(
            text(
                "DELETE FROM telegram_receipts "
                "WHERE id = CAST(:receipt_id AS uuid)"
            ),
            {"receipt_id": first.receipt_id},
        )


@pytest.mark.anyio
async def test_independent_privacy_purge_is_idempotent_and_fail_closed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(
        sessions,
        attempt_state="succeeded_unsettled",
        with_review=False,
        staged_expiry=NOW - timedelta(seconds=1),
    )
    confirmed = await seed_pilot(sessions)
    assert confirmed.review_id
    old_unprocessed = uuid4()
    async with sessions.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO telegram_receipts (
                    id, bot_key, update_id, payload, status, received_at
                ) VALUES (
                    :id, 'telegram_become_group', :update_id,
                    CAST(:private_payload AS jsonb),
                    'received', :received_at
                )
                """
            ),
            {
                "id": old_unprocessed,
                "update_id": old_unprocessed.int % 9_000_000_000_000_000_000,
                "received_at": NOW - timedelta(days=7, seconds=1),
                "private_payload": json.dumps(
                    {"message": {"text": "private rejected text"}}
                ),
            },
        )
        await session.execute(
            text(
                """
                UPDATE become_pilot_transcript_versions
                SET expires_at = :expired
                WHERE review_id = :review_id
                """
            ),
            {
                "expired": NOW - timedelta(seconds=1),
                "review_id": confirmed.review_id,
            },
        )
        await session.execute(
            text(
                """
                UPDATE become_media_intakes
                SET transcript_state = 'confirmed',
                    confirmed_transcript = 'confirmed private transcript',
                    transcript_digest = :digest,
                    transcript_confirmed_at = :confirmed_at,
                    transcript_expires_at = :expired
                WHERE id = :media_id
                """
            ),
            {
                "digest": DIGEST_1,
                "confirmed_at": NOW - timedelta(days=30),
                "expired": NOW - timedelta(seconds=1),
                "media_id": confirmed.media_id,
            },
        )

    store = SessionFactoryPilotTranscriptReviewStore(sessions)
    first = await store.purge_expired(now=NOW)
    media_first = await SessionFactoryBecomeMediaRetention(sessions).purge_expired(
        now=NOW
    )
    second = await store.purge_expired(now=NOW)
    media_second = await SessionFactoryBecomeMediaRetention(sessions).purge_expired(
        now=NOW
    )
    assert first["staged_transcripts_purged"] == 1
    assert first["transcript_versions_purged"] >= 1
    assert second["staged_transcripts_purged"] == 0
    assert second["transcript_versions_purged"] == 0
    assert media_first.transcripts_purged >= 1
    assert media_second.transcripts_purged == 0

    async with sessions() as session:
        staged = (
            await session.execute(
                text(
                    """
                    SELECT state, staged_transcript, transcript_digest,
                           provider_succeeded_at,
                           staged_transcript_expires_at, reason_code
                    FROM become_voice_transcription_attempts
                    WHERE id = :attempt_id
                    """
                ),
                {"attempt_id": graph.attempt_id},
            )
        ).one()
        version = (
            await session.execute(
                text(
                    """
                    SELECT transcript_body, transcript_digest, purged_at
                    FROM become_pilot_transcript_versions
                    WHERE review_id = :review_id
                    """
                ),
                {"review_id": confirmed.review_id},
            )
        ).one()
        confirmed_media = (
            await session.execute(
                text(
                    """
                    SELECT transcript_state, confirmed_transcript,
                           transcript_digest, transcript_purged_at
                    FROM become_media_intakes
                    WHERE id = :media_id
                    """
                ),
                {"media_id": confirmed.media_id},
            )
        ).one()
        payloads = (
            await session.execute(
                text(
                    """
                    SELECT payload
                    FROM telegram_receipts
                    WHERE id IN (:media_receipt, :old_unprocessed)
                    ORDER BY id
                    """
                ),
                {
                    "media_receipt": graph.media_receipt_id,
                    "old_unprocessed": old_unprocessed,
                },
            )
        ).scalars().all()
    assert staged[0] == "reconciliation_required"
    assert staged[1] is None
    assert staged[2] == DIGEST_1
    assert staged[3] == NOW - timedelta(days=31)
    assert staged[4] < NOW
    assert staged[5] == "become_transcription_staged_plaintext_expired"
    restarted_voice_store = SessionFactoryBecomeVoiceTranscriptionStore(sessions)
    assert await restarted_voice_store.confirm_success(
        attempt_id=str(graph.attempt_id),
        now=NOW,
    ) is False
    assert version[0] is None
    assert version[1] == DIGEST_1
    assert version[2] == NOW
    assert confirmed_media == ("purged", None, DIGEST_1, NOW)
    assert all(payload["content_purged"] is True for payload in payloads)
    assert all(
        payload["canonicalization_version"] == "telegram-json-c14n-v1"
        for payload in payloads
    )
    assert all(len(payload["canonical_payload_digest"]) == 64 for payload in payloads)
    assert all("message" not in payload for payload in payloads)


@pytest.mark.anyio
async def test_old_worker_rollback_preconditions_block_nonterminal_v2(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(
        sessions,
        attempt_state="succeeded_unsettled",
        with_review=False,
    )
    store = SessionFactoryPilotTranscriptReviewStore(sessions)
    status = await store.status(now=NOW)
    assert status["staged_transcript_reconciliation_required"] == 0
    preflight = await store.rollback_preflight()
    assert preflight["rollback_ready"] is False
    assert preflight["nonterminal_v2_provider_attempts"] >= 1
    assert preflight["staged_plaintext"] >= 1
    assert preflight["content_exposed"] is False
    async with sessions() as session:
        legacy_selector_count = await session.scalar(
            text(
                """
                SELECT count(*)
                FROM become_voice_transcription_attempts
                WHERE id = :attempt_id
                  AND grant_key =
                    'become-investor-demo-transcription-v1'
                  AND state IN (
                    'reserving', 'reserved', 'calling',
                    'succeeded_unsettled'
                  )
                """
            ),
            {"attempt_id": graph.attempt_id},
        )
    assert legacy_selector_count == 0



async def _approve_seeded_transcript(
    sessions: async_sessionmaker[AsyncSession],
) -> PilotGraph:
    graph = await seed_pilot(sessions)
    review = SessionFactoryPilotTranscriptReviewStore(sessions)
    presentation = await review.claim_delivery(now=NOW, max_operations=8)
    assert presentation is not None
    await review.settle_delivery(
        delivery_id=presentation.delivery_id,
        outcome="sent",
        telegram_message_id="slice2-seed",
        now=NOW,
    )
    assert graph.callback_ref is not None
    assert (
        await review.apply_callback(
            action="c",
            callback_ref=graph.callback_ref,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW,
        )
        is PilotReviewDisposition.APPROVED
    )
    confirmation = await review.claim_delivery(now=NOW, max_operations=8)
    assert confirmation is not None
    await review.settle_delivery(
        delivery_id=confirmation.delivery_id,
        outcome="sent",
        telegram_message_id="slice2-seed-confirmed",
        now=NOW,
    )
    return graph


@pytest.mark.anyio
async def test_slice2_program_is_singleton_restart_safe_and_owner_bound(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    from app.application.become_program_draft import ProgramDraftDisposition
    from app.infrastructure.become_program_draft import (
        SessionFactoryProgramDraftStore,
    )

    graph = await _approve_seeded_transcript(sessions)
    store = SessionFactoryProgramDraftStore(sessions)
    source = await store.reserve_from_confirmed_transcript(
        workspace_key=graph.workspace_key,
        now=NOW,
    )
    assert source is not None
    assert source.transcript_version == 1
    job = await store.claim_provider_call(draft_id=source.draft_id, now=NOW)
    assert job is not None
    await store.complete_provider_call(
        draft_id=source.draft_id,
        program_markdown="# Программа\n\n1. Первый шаг",
        input_tokens=10,
        output_tokens=20,
        provider_model="fixture-model",
        now=NOW,
    )
    restarted = SessionFactoryProgramDraftStore(sessions)
    assert (
        await restarted.reserve_from_confirmed_transcript(
            workspace_key=graph.workspace_key,
            now=NOW,
        )
        is None
    )
    assert await restarted.claim_provider_call(
        draft_id=source.draft_id,
        now=NOW,
    ) is None
    delivery = await restarted.claim_delivery(now=NOW, max_operations=6)
    assert delivery is not None
    await restarted.settle_delivery(
        delivery_id=delivery.delivery_id,
        outcome="sent",
        telegram_message_id="program-card",
        now=NOW,
    )
    with pytest.raises(
        BecomeControlledIntakeError,
        match="become_program_callback_binding_invalid",
    ):
        await restarted.apply_callback(
            action="c",
            callback_ref=delivery.callback_ref,
            subject_ref="ref://pilot/subject/wrong",
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW,
        )
    assert (
        await restarted.apply_callback(
            action="e",
            callback_ref=delivery.callback_ref,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW,
        )
        is ProgramDraftDisposition.CORRECTION_REQUESTED
    )
    assert await restarted.apply_correction(
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        correction_text="# Исправленная программа\n\n1. Точный шаг",
        now=NOW,
    )
    correction_prompt = await restarted.claim_delivery(now=NOW, max_operations=6)
    assert correction_prompt is not None
    assert correction_prompt.kind == "correction_prompt"
    await restarted.settle_delivery(
        delivery_id=correction_prompt.delivery_id,
        outcome="sent",
        telegram_message_id="program-correction-prompt",
        now=NOW,
    )
    redisplay = await restarted.claim_delivery(now=NOW, max_operations=6)
    assert redisplay is not None
    await restarted.settle_delivery(
        delivery_id=redisplay.delivery_id,
        outcome="sent",
        telegram_message_id="program-card-v2",
        now=NOW,
    )
    assert (
        await restarted.apply_callback(
            action="c",
            callback_ref=redisplay.callback_ref,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW,
        )
        is ProgramDraftDisposition.APPROVED
    )
    assert (
        await restarted.apply_callback(
            action="c",
            callback_ref=redisplay.callback_ref,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW,
        )
        is ProgramDraftDisposition.APPROVED_DUPLICATE
    )
    async with sessions() as session:
        assert await session.scalar(
            text(
                """
                SELECT count(*) FROM become_pilot_program_drafts
                WHERE transcript_review_id = :review_id
                """
            ),
            {"review_id": graph.review_id},
        ) == 1
        assert await session.scalar(
            text(
                """
                SELECT count(*) FROM become_pilot_program_versions
                WHERE draft_id = CAST(:draft_id AS uuid)
                """
            ),
            {"draft_id": source.draft_id},
        ) == 2


@pytest.mark.anyio
async def test_slice2_transcript_correction_invalidates_old_program_and_ttl_purges(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    from app.infrastructure.become_program_draft import (
        SessionFactoryProgramDraftStore,
    )

    graph = await _approve_seeded_transcript(sessions)
    program = SessionFactoryProgramDraftStore(sessions)
    source = await program.reserve_from_confirmed_transcript(
        workspace_key=graph.workspace_key,
        now=NOW,
    )
    assert source is not None
    assert await program.claim_provider_call(draft_id=source.draft_id, now=NOW)
    await program.complete_provider_call(
        draft_id=source.draft_id,
        program_markdown="# Старая программа",
        input_tokens=1,
        output_tokens=1,
        provider_model="fixture-model",
        now=NOW,
    )

    transcript = SessionFactoryPilotTranscriptReviewStore(sessions)
    assert graph.callback_ref is not None
    await transcript.apply_callback(
        action="e",
        callback_ref=graph.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW + timedelta(minutes=1),
    )
    assert await transcript.apply_correction(
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        correction_text="исправленный подтвержденный текст",
        now=NOW + timedelta(minutes=2),
    )
    correction_prompt = await transcript.claim_delivery(
        now=NOW + timedelta(minutes=3),
        max_operations=8,
    )
    assert correction_prompt is not None
    await transcript.settle_delivery(
        delivery_id=correction_prompt.delivery_id,
        outcome="sent",
        telegram_message_id="transcript-correction-prompt",
        now=NOW + timedelta(minutes=3),
    )
    transcript_card = await transcript.claim_delivery(
        now=NOW + timedelta(minutes=3),
        max_operations=8,
    )
    assert transcript_card is not None
    await transcript.settle_delivery(
        delivery_id=transcript_card.delivery_id,
        outcome="sent",
        telegram_message_id="transcript-v2",
        now=NOW + timedelta(minutes=3),
    )
    await transcript.apply_callback(
        action="c",
        callback_ref=transcript_card.callback_ref,
        subject_ref=graph.subject_ref,
        provider_dialog_id=graph.provider_dialog_id,
        now=NOW + timedelta(minutes=4),
    )
    replacement = await program.reserve_from_confirmed_transcript(
        workspace_key=graph.workspace_key,
        now=NOW + timedelta(minutes=5),
    )
    assert replacement is not None
    assert replacement.transcript_version == 2
    assert replacement.draft_id != source.draft_id
    async with sessions() as session:
        state = await session.scalar(
            text(
                """
                SELECT state FROM become_pilot_program_drafts
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": source.draft_id},
        )
    assert state == "invalidated"
    assert await program.purge_expired(
        now=NOW + timedelta(days=31),
    ) == 1
    async with sessions() as session:
        body = await session.scalar(
            text(
                """
                SELECT program_body FROM become_pilot_program_versions
                WHERE draft_id = CAST(:id AS uuid)
                """
            ),
            {"id": source.draft_id},
        )
    assert body is None

@pytest.mark.anyio
async def test_slice2_inflight_source_change_invalidates_without_delivery_or_retry(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A provider result crossing an approval change can never become displayable."""

    from app.infrastructure.become_program_draft import (
        SessionFactoryProgramDraftStore,
    )

    graph = await _approve_seeded_transcript(sessions)
    program = SessionFactoryProgramDraftStore(sessions)
    source = await program.reserve_from_confirmed_transcript(
        workspace_key=graph.workspace_key,
        now=NOW,
    )
    assert source is not None
    assert await program.claim_provider_call(draft_id=source.draft_id, now=NOW)

    provider_started = asyncio.Event()
    provider_may_return = asyncio.Event()

    async def controlled_provider_completion() -> None:
        provider_started.set()
        await provider_may_return.wait()
        await program.complete_provider_call(
            draft_id=source.draft_id,
            program_markdown="# Устаревшая программа",
            input_tokens=1,
            output_tokens=1,
            provider_model="barrier-fixture",
            now=NOW + timedelta(minutes=2),
        )

    provider_task = asyncio.create_task(controlled_provider_completion())
    await provider_started.wait()

    transcript = SessionFactoryPilotTranscriptReviewStore(sessions)
    assert graph.callback_ref is not None
    assert (
        await transcript.apply_callback(
            action="e",
            callback_ref=graph.callback_ref,
            subject_ref=graph.subject_ref,
            provider_dialog_id=graph.provider_dialog_id,
            now=NOW + timedelta(minutes=1),
        )
        is PilotReviewDisposition.CORRECTION_REQUESTED
    )

    provider_may_return.set()
    await provider_task

    restarted = SessionFactoryProgramDraftStore(sessions)
    assert await restarted.claim_provider_call(
        draft_id=source.draft_id,
        now=NOW + timedelta(minutes=3),
    ) is None
    assert await restarted.claim_delivery(
        now=NOW + timedelta(minutes=3),
        max_operations=6,
    ) is None

    async with sessions() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT state, error_code,
                           (SELECT count(*)
                            FROM become_pilot_program_versions v
                            WHERE v.draft_id = p.id) AS version_count,
                           (SELECT count(*)
                            FROM become_pilot_program_deliveries d
                            WHERE d.draft_id = p.id) AS delivery_count
                    FROM become_pilot_program_drafts p
                    WHERE p.id = CAST(:id AS uuid)
                    """
                ),
                {"id": source.draft_id},
            )
        ).mappings().one()
    assert row["state"] == "invalidated"
    assert row["error_code"] == "source_changed_in_flight"
    assert row["version_count"] == 0
    assert row["delivery_count"] == 0

@pytest.mark.anyio
async def test_active_grant_guard_and_clean_migration_round_trip_are_repeatable(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    graph = await seed_pilot(
        sessions,
        attempt_state="succeeded_unsettled",
        with_review=False,
    )
    migration_env = {**os.environ, "DATABASE_URL": _database_url()}
    blocked = subprocess.run(
        ["alembic", "downgrade", "0083_become_quest_corrective_m"],
        check=False,
        capture_output=True,
        text=True,
        env=migration_env,
    )
    assert blocked.returncode != 0
    assert (
        "refusing to remove active BECOME Pilot transcription grant"
        in blocked.stdout + blocked.stderr
    )
    async with sessions() as session:
        active_grants = await session.scalar(
            text(
                """
                SELECT count(*)
                FROM become_voice_transcription_attempts
                WHERE id = :attempt_id
                  AND grant_key = 'become-pilot-slice1-transcription-v2'
                """
            ),
            {"attempt_id": graph.attempt_id},
        )
    assert active_grants == 1

    await _clean_pilot_fixtures(sessions)
    await _clean_pilot_fixtures(sessions)
    try:
        subprocess.run(
            ["alembic", "downgrade", "-1"],
            check=True,
            env=migration_env,
        )
    finally:
        subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True,
            env=migration_env,
        )

    heads = subprocess.run(
        ["alembic", "heads"],
        check=True,
        capture_output=True,
        text=True,
        env=migration_env,
    ).stdout.strip().splitlines()
    assert len(heads) == 1
    assert heads[0].startswith("0116_watch_voice_observability")



