"""Synthetic exact rejected owner note; actual durable billing, zero external I/O."""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.application.telegram_voice_transcription import NormalizedVoice
from app.infrastructure.openai_transcription import _provider_error_code
from app.infrastructure.openai_transcription_parts import _error_code
from app.infrastructure.watch_voice_actions import drain, operational_status, validate_binding
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.infrastructure.watch_voice_recovery import recover_note
from app.models.billing import (
    BillingBudgetReservationModel,
    BillingEntitlementModel,
    BillingLedgerEntryModel,
    BillingUsageEventModel,
)
from app.models.canonical import Workspace
from app.models.watch_voice import WatchVoiceDrainRun, WatchVoiceNote, WatchVoiceRecoveryClaim
from app.worker import watch_voice as worker
from sqlalchemy import select

from test_watch_voice import database
from test_watch_voice_actions import ActionsWorld, action_settings, claims


async def seed(factory, world, *, hard_limit=3_000_000):
    now = datetime.now(UTC)
    received = now - timedelta(seconds=120)
    note_id = str(uuid4())
    workspace_id = uuid4()
    original_id = uuid4()
    async with factory() as session:
        for table in (
            Workspace.__table__,
            BillingEntitlementModel.__table__,
            BillingBudgetReservationModel.__table__,
            BillingUsageEventModel.__table__,
            BillingLedgerEntryModel.__table__,
            WatchVoiceDrainRun.__table__,
            WatchVoiceRecoveryClaim.__table__,
        ):
            await session.run_sync(lambda s, table=table: table.create(s.connection()))
        session.add(
            Workspace(
                id=workspace_id, key="mark-personal", display_name="Synthetic owner", is_active=True
            )
        )
        await session.flush()
        session.add(
            BillingEntitlementModel(
                workspace_id=workspace_id,
                entitlement_key="synthetic-speech-budget",
                product="gate",
                scope_kind="telegram_connector",
                scope_ref="telegram_business",
                plan_key="synthetic",
                state="active",
                valid_from=received - timedelta(days=1),
                valid_until=None,
                hard_limit_microusd=hard_limit,
            )
        )
        session.add(
            BillingBudgetReservationModel(
                id=original_id,
                workspace_id=workspace_id,
                reservation_key=f"watch-voice:{note_id}",
                product="gate",
                operation="transcription",
                scope_kind="telegram_connector",
                scope_ref="telegram_business",
                period_key=received.strftime("%Y-%m"),
                reserved_microusd=5_000,
                settled_microusd=None,
                state="released",
                error_code=None,
                created_at=received,
                updated_at=received,
            )
        )
        session.add(
            WatchVoiceNote(
                id=note_id,
                workspace_key="mark-personal",
                account_id="synthetic-owner",
                file_id=world.entry["id"],
                revision=world.entry["rev"],
                source_path=world.entry["path_display"],
                filename="Synthetic.m4a",
                media_type="audio/mp4",
                size_bytes=len(world.audio),
                sha256=hashlib.sha256(world.audio).hexdigest(),
                data=world.audio,
                duration_seconds=5,
                recorded_at=None,
                dropbox_modified_at=received,
                received_at=received,
                transcription_state="rejected",
                transcript=None,
                model="gpt-4o-mini-transcribe",
                transcribed_at=received,
                delivery_state="pending",
                next_part=0,
                issue_number=None,
                issue_url=None,
                github_received_at=None,
                delivered_at=None,
                attempted_at=received,
                last_error="openai_transcription_rate_limited",
            )
        )
        await session.commit()
    world.binding.update(
        {
            "scope": "initial-quota-probe",
            "event_id": note_id,
            "expected_audio_sha256": hashlib.sha256(world.audio).hexdigest(),
            "expected_error": "openai_transcription_rate_limited",
            "owner_quota_probe": True,
            "authorized_at": (now - timedelta(seconds=30))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "provider_cause": "insufficient_quota",
        }
    )
    return note_id, original_id


class RecoveryWorld(ActionsWorld):
    def __init__(self):
        super().__init__()
        self.provider_mode = "success"
        self.requests = []

    def handle(self, request):
        if request.url.path == "/v1/audio/transcriptions":
            self.provider_calls += 1
            self.requests.append(request)
            assert request.url == "https://api.openai.com/v1/audio/transcriptions"
            assert request.extensions["timeout"]["read"] == 60
            assert b"gpt-4o-transcribe" in request.content
            assert b"gpt-4o-mini-transcribe" not in request.content
            assert b'"tools"' not in request.content
            if self.provider_mode == "quota":
                return httpx.Response(
                    429,
                    json={
                        "error": {
                            "type": "insufficient_quota",
                            "code": "insufficient_quota",
                            "message": "synthetic-private-message-never-log",
                        }
                    },
                )
            if self.provider_mode == "timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if self.provider_mode == "500":
                return httpx.Response(500, json={"error": {"message": "synthetic"}})
            return httpx.Response(
                200,
                json={
                    "text": self.transcript,
                    "usage": {
                        "type": "tokens",
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "total_tokens": 12,
                    },
                },
                headers={"x-request-id": "synthetic-request"},
            )
        if "/files/" in request.url.path or "/oauth2/" in request.url.path:
            raise AssertionError("Recovery must never import/download Dropbox")
        return super().handle(request)

    def normalizer(self, monkeypatch, *, multipart=False):
        more = (NormalizedVoice(b"second", "second.mp3", "audio/mpeg"),) if multipart else ()
        monkeypatch.setattr(
            worker.TelegramVoiceNormalizer,
            "normalize",
            AsyncMock(
                return_value=NormalizedVoice(self.audio, "synthetic.mp3", "audio/mpeg", more)
            ),
        )


async def call(factory, world, run_id="321"):
    world.current_run = run_id
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
        return await drain(
            settings=action_settings(),
            factory=factory,
            http=http,
            claims=claims(run_id=run_id, jti="synthetic-jti-" + run_id),
            job_token="synthetic-job-token",
        )


def test_owner_quota_probe_binding_is_strict_and_default_initial_is_unchanged(tmp_path):
    async def run():
        world = RecoveryWorld()
        assert validate_binding(world.binding)["scope"] == "initial-exact"
        async with database(tmp_path) as factory:
            await seed(factory, world)
            assert validate_binding(world.binding)["scope"] == "initial-quota-probe"
            for change in (
                {"owner_quota_probe": False},
                {"owner_quota_probe": "true"},
                {"authorized_at": "tomorrow"},
                {
                    "authorized_at": (datetime.now(UTC) + timedelta(days=1)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                },
                {"quota_restored": True},
                {"scope": "initial-recovery"},
                {"expected_error": []},
                {"provider_cause": "rate_limit_exceeded"},
                {"model": "other"},
                {"workspace_key": "foreign"},
                {"expected_audio_sha256": "bad"},
                {"event_id": "bad"},
            ):
                with pytest.raises(WatchVoiceError):
                    validate_binding({**world.binding, **change})

    asyncio.run(run())


def test_success_preserves_original_billing_audit_and_replays_once(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, original_id = await seed(factory, world)
            first = await call(factory, world)
            assert first["delivered"] == 1 and first["published_parts"] == 1
            assert set(first) == {
                "schema",
                "result",
                "considered",
                "published_parts",
                "delivered",
                "reconciled",
                "private_content_exposed",
            }
            for run_id in ("322", "323", "324"):
                assert (await call(factory, world, run_id))["published_parts"] == 0
            assert world.provider_calls == 1 and world.posts == 1
            async with factory() as session:
                note = await session.get(WatchVoiceNote, note_id)
                audit = await session.get(WatchVoiceRecoveryClaim, note_id)
                original = await session.get(BillingBudgetReservationModel, original_id)
                new = await session.scalar(
                    select(BillingBudgetReservationModel).where(
                        BillingBudgetReservationModel.reservation_key
                        == f"watch-voice:{note_id}:recovery1"
                    )
                )
                assert note.data == world.audio and note.transcript == world.transcript
                assert note.model == "gpt-4o-transcribe" and note.delivery_state == "delivered"
                assert audit.outcome == "transcribed" and audit.finished_at
                assert audit.authorization_kind == "owner_quota_probe"
                assert audit.authorized_at is not None
                assert audit.original_error == "openai_transcription_rate_limited"
                assert audit.original_model == "gpt-4o-mini-transcribe"
                assert audit.original_reservation_id == original_id
                assert audit.authorization_sha == claims()["sha"]
                assert original.state == "released" and original.settled_microusd is None
                assert new.state == "settled" and new.settled_microusd == 45
                from app.infrastructure.watch_voice_recovery import protected_recovery_receipt

                receipt = await protected_recovery_receipt(session, note)
                assert receipt["original_reservation_released"] is True
                assert receipt["provider_readiness"] == "available_for_attempt"
                assert "quota_restored_at" not in receipt
                assert receipt["actual_microusd"] == 45
                assert receipt["reservation_state"] == "settled"
                assert receipt["original_attempted_at"] != receipt["claimed_at"]
                assert receipt["finished_at"] and receipt["outcome"] == "transcribed"
                status = await operational_status(session, action_settings(), scope_allowed=True)
                assert status["recovery_counts"] == {"transcribed": 1}
                from app.api import mcp_routes

                principal = SimpleNamespace(
                    access_scope=SimpleNamespace(
                        workspace_id="mark-personal",
                        project_ids={mcp_routes.UNCLASSIFIED_PROJECT_ID},
                    )
                )
                tool_receipt = await mcp_routes._watch_voice_tool_result(
                    request_id="synthetic",
                    selector="watch-voice:" + note_id,
                    principal=principal,
                    session=session,
                    settings=action_settings(),
                )
                protected = json.loads(tool_receipt["result"]["content"][0]["text"])
                assert protected["recovery"] == receipt
                assert protected["model"] == "gpt-4o-transcribe"
                principal.access_scope.workspace_id = "foreign"
                denied_session = AsyncMock()
                denied = await mcp_routes._watch_voice_tool_result(
                    request_id="synthetic",
                    selector="watch-voice:" + note_id,
                    principal=principal,
                    session=denied_session,
                    settings=action_settings(),
                )
                assert denied["result"]["isError"] is True
                denied_session.scalar.assert_not_called()
                denied_session.get.assert_not_called()
            assert world.transcript in world.issues[0]["body"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode,expected,billing",
    [
        ("quota", "rejected", "released"),
        ("timeout", "unknown", "unknown"),
        ("500", "unknown", "unknown"),
    ],
)
def test_rejected_or_unknown_recovery_is_never_called_again(
    tmp_path, monkeypatch, mode, expected, billing, caplog
):
    world = RecoveryWorld()
    world.provider_mode = mode
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            for run_id in ("321", "322", "323"):
                assert (await call(factory, world, run_id))["delivered"] == 0
            async with factory() as session:
                note = await session.get(WatchVoiceNote, note_id)
                audit = await session.get(WatchVoiceRecoveryClaim, note_id)
                reservation = await session.scalar(
                    select(BillingBudgetReservationModel).where(
                        BillingBudgetReservationModel.reservation_key
                        == f"watch-voice:{note_id}:recovery1"
                    )
                )
                assert note.transcription_state == expected and audit.outcome == expected
                assert reservation.state == billing
                if mode == "quota":
                    assert note.last_error == "openai_transcription_quota_unavailable"
                    from app.infrastructure.watch_voice_recovery import protected_recovery_receipt

                    assert (await protected_recovery_receipt(session, note))[
                        "provider_readiness"
                    ] == "quota_unavailable"
            assert world.provider_calls == 1 and world.posts == 0

    asyncio.run(run())
    assert "synthetic-private-message-never-log" not in caplog.text
    assert "synthetic-openai" not in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_key", "foreign"),
        ("account_id", "foreign"),
        ("file_id", "id:foreign"),
        ("revision", "foreign"),
        ("sha256", "b" * 64),
        ("data", b"corrupted"),
        ("transcription_state", "unknown"),
        ("transcription_state", "processing"),
        ("transcription_state", "transcribed"),
        ("transcript", "existing"),
        ("model", "gpt-4o-transcribe"),
        ("delivery_state", "unknown"),
        ("issue_number", 42),
        ("next_part", 1),
        ("last_error", "other"),
    ],
)
def test_wrong_or_consumed_note_fails_before_provider(tmp_path, monkeypatch, field, value):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            async with factory() as session:
                note = await session.get(WatchVoiceNote, note_id)
                setattr(note, field, value)
                await session.commit()
            with pytest.raises(WatchVoiceError):
                await call(factory, world)
            assert world.provider_calls == 0 and world.posts == 0
            async with factory() as session:
                assert await session.get(WatchVoiceRecoveryClaim, note_id) is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "reserved"),
        ("state", "unknown"),
        ("state", "settled"),
        ("product", "alpha"),
        ("scope_ref", "foreign"),
        ("operation", "model_response"),
        ("period_key", "2000-01"),
        ("reservation_key", "foreign"),
    ],
)
def test_wrong_original_reservation_fails_closed(tmp_path, monkeypatch, field, value):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, original_id = await seed(factory, world)
            async with factory() as session:
                reservation = await session.get(BillingBudgetReservationModel, original_id)
                setattr(reservation, field, value)
                if value == "unknown":
                    reservation.error_code = "synthetic-unknown"
                if value == "settled":
                    reservation.settled_microusd = 1
                await session.commit()
            with pytest.raises(WatchVoiceError, match="released_reservation"):
                await call(factory, world)
            assert world.provider_calls == 0 and world.posts == 0
            async with factory() as session:
                assert await session.get(WatchVoiceRecoveryClaim, note_id) is None

    asyncio.run(run())


def test_budget_denial_before_provider_permanently_consumes_cap(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, original_id = await seed(factory, world)
            async with factory() as session:
                original = await session.get(BillingBudgetReservationModel, original_id)
                session.add(
                    BillingBudgetReservationModel(
                        workspace_id=original.workspace_id,
                        reservation_key="synthetic-budget-held",
                        product="gate",
                        operation="transcription",
                        scope_kind="telegram_connector",
                        scope_ref="telegram_business",
                        period_key=original.period_key,
                        reserved_microusd=3_000_000,
                        settled_microusd=None,
                        state="reserved",
                        error_code=None,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            await call(factory, world)
            await call(factory, world, "322")
            async with factory() as session:
                note = await session.get(WatchVoiceNote, note_id)
                audit = await session.get(WatchVoiceRecoveryClaim, note_id)
                assert note.last_error == "billing_hard_limit_exceeded"
                assert audit.outcome == "rejected"
            assert world.provider_calls == 0 and world.posts == 0

    asyncio.run(run())


def test_multipart_recovery_consumes_cap_without_provider(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.normalizer(monkeypatch, multipart=True)

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            await call(factory, world)
            await call(factory, world, "322")
            async with factory() as session:
                note = await session.get(WatchVoiceNote, note_id)
                assert note.last_error == "watch_voice_recovery_single_part_required"
            assert world.provider_calls == 0 and world.posts == 0

    asyncio.run(run())


def test_claim_crash_then_stale_is_unknown_without_provider(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)
    original = worker.transcribe_claimed_note
    monkeypatch.setattr(
        worker, "transcribe_claimed_note", AsyncMock(side_effect=asyncio.CancelledError)
    )

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            with pytest.raises(asyncio.CancelledError):
                await call(factory, world)
            monkeypatch.setattr(worker, "transcribe_claimed_note", original)
            async with factory() as session:
                audit = await session.get(WatchVoiceRecoveryClaim, note_id)
                audit.claimed_at = datetime.now(UTC) - timedelta(hours=2)
                await session.commit()
            await call(factory, world, "322")
            async with factory() as session:
                note = await session.get(WatchVoiceNote, note_id)
                audit = await session.get(WatchVoiceRecoveryClaim, note_id)
                assert note.transcription_state == audit.outcome == "unknown"
                assert audit.finished_at is not None
            assert world.provider_calls == 0 and world.posts == 0

    asyncio.run(run())


def test_lost_ack_reconciles_one_post_and_no_new_transcription(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.fail_post = "lost_ack"
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory, world)
            first = await call(factory, world)
            assert first["delivered"] == 0
            second = await call(factory, world, "322")
            assert second["delivered"] == 1 and second["reconciled"] == 1
            assert world.provider_calls == 1 and world.posts == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"owner_quota_probe": False},
        {"authorized_at": "2000-01-01T00:00:00Z"},
        {"expected_audio_sha256": "a" * 64},
    ],
)
def test_absent_or_stale_quota_approval_never_claims_note(tmp_path, monkeypatch, change):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            world.binding.update(change)
            with pytest.raises(WatchVoiceError):
                await call(factory, world)
            async with factory() as session:
                assert await session.get(WatchVoiceRecoveryClaim, note_id) is None
                assert (
                    await session.get(WatchVoiceNote, note_id)
                ).transcription_state == "rejected"
            assert world.provider_calls == world.posts == 0

    asyncio.run(run())


def test_private_target_preflight_failure_before_recovery_claim(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.public_repo = True
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            with pytest.raises(WatchVoiceError):
                await call(factory, world)
            async with factory() as session:
                assert await session.get(WatchVoiceRecoveryClaim, note_id) is None
                assert (await session.scalars(select(WatchVoiceDrainRun))).all() == []
            assert world.provider_calls == world.posts == 0

    asyncio.run(run())


def test_large_transcript_continues_one_part_without_retranscribing(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.transcript = ("Синтетический длинный текст. " * 2500).strip()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            first = await call(factory, world)
            assert first["published_parts"] == 1 and first["delivered"] == 0
            for count in range(20):
                receipt = await call(factory, world, str(322 + count))
                assert receipt["published_parts"] <= 1
                if receipt["delivered"]:
                    break
            else:
                pytest.fail("bounded continuation did not complete")
            async with factory() as session:
                assert (await session.get(WatchVoiceNote, note_id)).delivery_state == "delivered"
            assert world.provider_calls == 1
            assert len(world.issues) == 1 and len(world.comments) > 0
            reconstructed = [world.issues[0]["body"], *(c["body"] for c in world.comments)]
            from app.infrastructure.watch_voice_github import transcript_parts

            for part, body in zip(transcript_parts(world.transcript), reconstructed, strict=True):
                assert part in body

    asyncio.run(run())


def test_crash_after_stored_transcript_finishes_audit_without_provider_retry(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)
    original = worker.transcribe_claimed_note

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "transcribe_claimed_note", interrupted)

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            with pytest.raises(asyncio.CancelledError):
                await call(factory, world)
            monkeypatch.setattr(worker, "transcribe_claimed_note", original)
            assert (await call(factory, world, "322"))["delivered"] == 1
            async with factory() as session:
                assert (
                    await session.get(WatchVoiceRecoveryClaim, note_id)
                ).outcome == "transcribed"
            assert world.provider_calls == world.posts == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "error,quota",
    [
        ({"type": "insufficient_quota", "code": "OTHER"}, True),
        ({"code": "organization_usage_limit_exceeded"}, True),
        ({"code": "insufficient_quota"}, True),
        ({"type": "rate_limit_error", "code": "rate_limit_exceeded"}, False),
        ({"message": "insufficient_quota"}, False),
        ({"code": ["insufficient_quota"]}, False),
    ],
)
def test_typed_quota_mapping_preserves_unknown_and_uses_no_message(error, quota):
    expected = (
        "openai_transcription_quota_unavailable" if quota else "openai_transcription_rate_limited"
    )
    response = httpx.Response(429, json={"error": error})
    assert _provider_error_code(response) == _error_code(response) == expected
    assert _provider_error_code(httpx.Response(500)) == "openai_transcription_provider_unknown"


def test_actual_postgres_parallel_recovery_claim_one_call(tmp_path, monkeypatch):
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("actual PostgreSQL DATABASE_URL required for recovery row-lock concurrency")
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            note_id, _ = await seed(factory, world)
            from app.infrastructure.watch_voice_actions import bound_settings

            scoped = await bound_settings(
                action_settings(), world.binding, factory, job_token="synthetic-job-token"
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await asyncio.gather(
                    *(
                        recover_note(
                            scoped, factory, http, world.binding, authorization_sha=claims()["sha"]
                        )
                        for _ in range(6)
                    )
                )
            async with factory() as session:
                assert (
                    await session.get(WatchVoiceNote, note_id)
                ).transcription_state == "transcribed"
                assert len((await session.scalars(select(WatchVoiceRecoveryClaim))).all()) == 1
            assert world.provider_calls == 1 and world.posts == 0

    asyncio.run(run())
