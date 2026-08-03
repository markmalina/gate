"""Non-skipped PostgreSQL safety proofs for BECOME Pilot Slice 1."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
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
from app.application.telegram_voice_transcription import (
    NormalizedVoice,
    ProviderTranscript,
    SpeechToTextPort,
)
from app.infrastructure.become_transcript_review import (
    SessionFactoryPilotTranscriptReviewStore,
)
from app.infrastructure.become_voice_transcription import (
    SessionFactoryBecomeVoiceTranscriptionStore,
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


async def seed_pilot(
    sessions: async_sessionmaker[AsyncSession],
    *,
    attempt_state: str = "confirmed",
    with_review: bool = True,
    staged_expiry: datetime | None = None,
) -> PilotGraph:
    workspace_id = uuid4()
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
    staged = "provider private transcript" if attempt_state == "succeeded_unsettled" else None
    provider_started = (
        None
        if attempt_state in {"reserving", "reserved", "blocked"}
        else NOW - timedelta(minutes=2)
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
                "dialog": CHAT,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO agent_installations (
                    id, workspace_id, connector_id, dialog_id,
                    installation_key, dialog_label, state
                ) VALUES (
                    :id, :workspace_id, :connector_id, :dialog_id,
                    :key, 'Pilot fixture', 'active'
                )
                """
            ),
            {
                "id": installation_id,
                "workspace_id": workspace_id,
                "connector_id": connector_id,
                "dialog_id": dialog_id,
                "key": f"pilot-{installation_id}",
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
                    'owner_only', 2, '{"external_effects":0}'::jsonb, :now
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
                "subject_ref": SUBJECT,
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
                    'owner_only', 2, '{"external_effects":0}'::jsonb, :now
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
                "subject_ref": SUBJECT,
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
                    '{"external_effects":0}'::jsonb, :execution_digest,
                    '{"external_effects":0}'::jsonb, :event_id,
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
                "subject_ref": SUBJECT,
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
                    "provider_succeeded": NOW if success_state else None,
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
                    "subject_ref": SUBJECT,
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
    )


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
        return BudgetAdmission(True, "billing_reserved", self.current)

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
        self.current = self.current.__class__(
            **{
                **self.current.__dict__,
                "state": BudgetReservationState.SETTLED,
                "settled_microusd": 20,
            }
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
        "idle",
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
    with pytest.raises(BecomeControlledIntakeError):
        await store.owner_reconcile_delivery(
            delivery_id=retry.delivery_id,
            action="authorize_retry",
            evidence_digest="b" * 64,
            external_message_id=None,
            owner_authorized=True,
            now=NOW,
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
                    '{"message":{"text":"private rejected text"}}'::jsonb,
                    'received', :received_at
                )
                """
            ),
            {
                "id": old_unprocessed,
                "update_id": old_unprocessed.int % 9_000_000_000_000_000_000,
                "received_at": NOW - timedelta(days=7, seconds=1),
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
    second = await store.purge_expired(now=NOW)
    assert first["staged_transcripts_purged"] == 1
    assert first["transcript_versions_purged"] >= 1
    assert second["staged_transcripts_purged"] == 0
    assert second["transcript_versions_purged"] == 0

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
    assert staged[3] == NOW
    assert staged[4] < NOW
    assert staged[5] == "become_transcription_staged_plaintext_expired"
    assert version[0] is None
    assert version[1] == DIGEST_1
    assert version[2] == NOW
    assert all(payload["content_purged"] is True for payload in payloads)
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
    async with sessions() as session:
        blockers = (
            await session.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE state IN (
                          'reserving', 'reserved', 'calling',
                          'succeeded_unsettled'
                        )
                      ) AS nonterminal_v2,
                      count(*) FILTER (
                        WHERE staged_transcript IS NOT NULL
                      ) AS staged_plaintext
                    FROM become_voice_transcription_attempts
                    WHERE grant_key =
                      'become-pilot-slice1-transcription-v2'
                      AND workspace_id = :workspace_id
                    """
                ),
                {"workspace_id": graph.workspace_id},
            )
        ).one()
    assert blockers == (1, 1)
