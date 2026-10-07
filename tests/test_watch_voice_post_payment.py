"""Synthetic paid continuation: durable prior quota rejection, no external I/O."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.infrastructure.watch_voice_actions import bound_settings, validate_binding
from app.infrastructure.watch_voice_github import WatchVoiceError
from app.infrastructure.watch_voice_post_payment import (
    continue_note,
    protected_post_payment_receipt,
)
from app.models.billing import BillingBudgetReservationModel, BillingEntitlementModel
from app.models.watch_voice import (
    WatchVoiceNote,
    WatchVoicePostPaymentClaim,
    WatchVoiceRecoveryClaim,
)
from app.worker import watch_voice as worker
from sqlalchemy import select

from test_watch_voice import database
from test_watch_voice_actions import action_settings, claims
from test_watch_voice_recovery import RecoveryWorld, call, seed


def utc(value):
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def timestamp(value):
    return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def snapshot(row):
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


async def reservation(session, note_id, suffix):
    return await session.scalar(
        select(BillingBudgetReservationModel).where(
            BillingBudgetReservationModel.reservation_key == f"watch-voice:{note_id}:{suffix}"
        )
    )


async def previous_quota(factory, world):
    """Use the actual old claim/STT/billing path before authorizing a distinct continuation."""
    world.provider_mode = "quota"
    note_id, original_id = await seed(factory, world)
    assert (await call(factory, world))["delivered"] == 0
    assert world.provider_calls == 1 and world.posts == 0
    old_binding = dict(world.binding)
    async with factory() as session:
        note = await session.get(WatchVoiceNote, note_id)
        old = await session.get(WatchVoiceRecoveryClaim, note_id)
        original = await session.get(BillingBudgetReservationModel, original_id)
        previous = await reservation(session, note_id, "recovery1")
        assert note.last_error == old.last_error == "openai_transcription_quota_unavailable"
        assert note.transcription_state == old.outcome == "rejected"
        assert old.finished_at and previous.state == "released"
        assert await session.get(WatchVoicePostPaymentClaim, note_id) is None
        context = SimpleNamespace(
            note_id=note_id,
            original_id=original_id,
            original=snapshot(original),
            previous=snapshot(previous),
            old=snapshot(old),
            old_binding=old_binding,
            data=note.data,
            sha256=note.sha256,
            previous_attempted_at=note.attempted_at,
            note_received_at=note.received_at,
        )
        approval = max(
            datetime.now(UTC),
            utc(old.claimed_at) + timedelta(microseconds=1),
            utc(old.finished_at) + timedelta(microseconds=1),
            utc(note.attempted_at) + timedelta(microseconds=1),
        )
    world.binding = {
        **{key: value for key, value in old_binding.items() if key != "owner_quota_probe"},
        "scope": "initial-post-payment",
        "expected_error": "openai_transcription_quota_unavailable",
        "owner_post_payment": True,
        "authorized_at": timestamp(approval),
        "provider_cause": "credit_balance_exhausted",
    }
    world.provider_mode = "success"
    return context


async def assert_history_preserved(factory, context):
    async with factory() as session:
        assert snapshot(await session.get(WatchVoiceRecoveryClaim, context.note_id)) == context.old
        assert (
            snapshot(await session.get(BillingBudgetReservationModel, context.original_id))
            == context.original
        )
        assert (
            snapshot(await reservation(session, context.note_id, "recovery1")) == context.previous
        )
        note = await session.get(WatchVoiceNote, context.note_id)
        assert note.data == context.data and note.sha256 == context.sha256


def test_closed_post_payment_binding_requires_new_owner_scope_and_specific_prior_cause(
    tmp_path, monkeypatch
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            await previous_quota(factory, world)
            assert validate_binding(world.binding) == world.binding
            for change in (
                {"owner_post_payment": False},
                {"owner_post_payment": "true"},
                {"owner_quota_probe": True},
                {"provider_cause": "insufficient_quota"},
                {"provider_cause": "organization_spend_limit_exceeded"},
                {"expected_error": "openai_transcription_rate_limited"},
                {"expected_error": "openai_transcription_provider_unknown"},
                {"expected_error": []},
                {"authorized_at": "tomorrow"},
                {"authorized_at": timestamp(datetime.now(UTC) + timedelta(minutes=1))},
                {"model": "gpt-4o-mini-transcribe"},
                {"body": "synthetic-user-instruction"},
                {"url": "https://foreign.invalid"},
                {"workspace_key": "foreign"},
                {"event_id": "bad"},
                {"expected_audio_sha256": "bad"},
            ):
                with pytest.raises(WatchVoiceError):
                    validate_binding({**world.binding, **change})
            for key in ("event_id", "expected_error", "owner_post_payment", "provider_cause"):
                with pytest.raises(WatchVoiceError):
                    validate_binding({k: v for k, v in world.binding.items() if k != key})
            assert world.provider_calls == 1 and world.posts == 0

    asyncio.run(run())


def test_one_paid_attempt_delivers_one_issue_and_preserves_both_previous_audits(
    tmp_path, monkeypatch, caplog
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            first = await call(factory, world, "322")
            assert first["delivered"] == first["published_parts"] == 1
            assert set(first) == {
                "schema",
                "result",
                "considered",
                "published_parts",
                "delivered",
                "reconciled",
                "private_content_exposed",
            }
            assert first["private_content_exposed"] is False
            assert world.transcript not in json.dumps(first)
            for run_id in ("323", "324", "325"):
                world.binding["authorized_at"] = timestamp(datetime.now(UTC))
                assert (await call(factory, world, run_id))["published_parts"] == 0
            assert world.provider_calls == 2 and world.posts == len(world.issues) == 1
            assert world.comments == [] and world.transcript in world.issues[0]["body"]
            async with factory() as session:
                note = await session.get(WatchVoiceNote, context.note_id)
                audit = await session.get(WatchVoicePostPaymentClaim, context.note_id)
                paid = await reservation(session, context.note_id, "postpayment1")
                assert note.transcript == world.transcript and note.delivery_state == "delivered"
                assert note.model == audit.previous_model == "gpt-4o-transcribe"
                assert audit.previous_error == "openai_transcription_quota_unavailable"
                assert audit.previous_reservation_id == context.previous["id"]
                assert audit.previous_attempted_at == context.previous_attempted_at
                assert audit.authorization_kind == "owner_post_payment"
                assert audit.authorization_sha == claims()["sha"]
                assert utc(audit.authorized_at) > utc(context.old["finished_at"])
                assert audit.outcome == "transcribed" and audit.finished_at
                assert paid.state == "settled" and paid.settled_microusd == 45
                assert paid.reservation_key == f"watch-voice:{context.note_id}:postpayment1"
                assert len((await session.scalars(select(WatchVoicePostPaymentClaim))).all()) == 1
            await assert_history_preserved(factory, context)
            # Reinstating the old binding cannot extend either permanent provider cap.
            world.binding = context.old_binding
            assert (await call(factory, world, "326"))["published_parts"] == 0
            assert world.provider_calls == 2 and world.posts == 1
            await assert_history_preserved(factory, context)

    asyncio.run(run())
    for private in ("synthetic-private-message-never-log", "synthetic-openai", world.transcript):
        assert private not in caplog.text


@pytest.mark.parametrize(
    "mode,outcome,billing",
    [
        ("quota", "rejected", "released"),
        ("timeout", "unknown", "unknown"),
        ("500", "unknown", "unknown"),
    ],
)
def test_rejected_or_unknown_post_payment_permanently_consumes_new_cap(
    tmp_path, monkeypatch, mode, outcome, billing, caplog
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            world.provider_mode = mode
            assert (await call(factory, world, "322"))["delivered"] == 0
            world.provider_mode = "success"
            for run_id in ("323", "324", "325"):
                assert (await call(factory, world, run_id))["published_parts"] == 0
            async with factory() as session:
                note = await session.get(WatchVoiceNote, context.note_id)
                audit = await session.get(WatchVoicePostPaymentClaim, context.note_id)
                paid = await reservation(session, context.note_id, "postpayment1")
                assert note.transcription_state == audit.outcome == outcome
                assert paid.state == billing and audit.finished_at is not None
                assert note.transcript is None
            assert world.provider_calls == 2 and world.posts == 0
            await assert_history_preserved(factory, context)

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
        ("size_bytes", 1),
        ("source_path", "/Alpha/Other/Synthetic.m4a"),
        ("duration_seconds", None),
        ("transcript", "preexisting synthetic transcript"),
        ("transcription_state", "unknown"),
        ("model", "gpt-4o-mini-transcribe"),
        ("last_error", "openai_transcription_rate_limited"),
        ("delivery_state", "unknown"),
        ("next_part", 1),
        ("issue_number", 101),
    ],
)
def test_changed_archive_or_previous_attempt_never_creates_new_claim(
    tmp_path, monkeypatch, field, value
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            async with factory() as session:
                setattr(await session.get(WatchVoiceNote, context.note_id), field, value)
                await session.commit()
            with pytest.raises(WatchVoiceError):
                await call(factory, world, "322")
            async with factory() as session:
                assert await session.get(WatchVoicePostPaymentClaim, context.note_id) is None
                assert (
                    snapshot(await session.get(WatchVoiceRecoveryClaim, context.note_id))
                    == context.old
                )
            assert world.provider_calls == 1 and world.posts == 0

    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["claimed_at", "finished_at", "note_attempted_at"])
def test_authorization_must_follow_every_previous_attempt_boundary(tmp_path, monkeypatch, boundary):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            previous = (
                context.previous_attempted_at
                if boundary == "note_attempted_at"
                else context.old[boundary]
            )
            world.binding["authorized_at"] = timestamp(previous)
            with pytest.raises(WatchVoiceError):
                await call(factory, world, "322")
            async with factory() as session:
                assert await session.get(WatchVoicePostPaymentClaim, context.note_id) is None
            assert world.provider_calls == 1 and world.posts == 0
            await assert_history_preserved(factory, context)

    asyncio.run(run())


@pytest.mark.parametrize("change", ["missing", "unknown", "claimed", "no_finished_at"])
def test_missing_or_nondefinite_prior_recovery_never_authorizes_post_payment(
    tmp_path, monkeypatch, change
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            async with factory() as session:
                old = await session.get(WatchVoiceRecoveryClaim, context.note_id)
                if change == "missing":
                    await session.delete(old)
                elif change == "no_finished_at":
                    old.finished_at = None
                else:
                    old.outcome = change
                await session.commit()
            with pytest.raises(WatchVoiceError):
                await call(factory, world, "322")
            async with factory() as session:
                assert await session.get(WatchVoicePostPaymentClaim, context.note_id) is None
            assert world.provider_calls == 1 and world.posts == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "reserved"),
        ("state", "unknown"),
        ("state", "settled"),
        ("scope_ref", "foreign"),
        ("operation", "model_response"),
        ("product", "alpha"),
        ("period_key", "2000-01"),
    ],
)
def test_prior_recovery_reservation_must_be_exact_and_definitely_released(
    tmp_path, monkeypatch, field, value
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            async with factory() as session:
                previous = await reservation(session, context.note_id, "recovery1")
                setattr(previous, field, value)
                if value == "settled":
                    previous.settled_microusd = 1
                if value == "unknown":
                    previous.error_code = "synthetic-unknown"
                await session.commit()
            with pytest.raises(WatchVoiceError):
                await call(factory, world, "322")
            async with factory() as session:
                assert await session.get(WatchVoicePostPaymentClaim, context.note_id) is None
            assert world.provider_calls == 1 and world.posts == 0

    asyncio.run(run())


@pytest.mark.parametrize("hard_limit", [1_000_000, 3_000_001])
def test_changed_shared_entitlement_ceiling_never_enters_provider(
    tmp_path, monkeypatch, hard_limit
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            async with factory() as session:
                entitlement = await session.scalar(select(BillingEntitlementModel))
                entitlement.hard_limit_microusd = hard_limit
                await session.commit()
            with pytest.raises(WatchVoiceError):
                await call(factory, world, "322")
            async with factory() as session:
                assert await session.get(WatchVoicePostPaymentClaim, context.note_id) is None
            assert world.provider_calls == 1 and world.posts == 0
            await assert_history_preserved(factory, context)

    asyncio.run(run())


def test_shared_budget_denial_is_permanent_even_if_budget_later_becomes_available(
    tmp_path, monkeypatch
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            async with factory() as session:
                held = BillingBudgetReservationModel(
                    workspace_id=context.original["workspace_id"],
                    reservation_key="synthetic-post-payment-shared-budget-held",
                    product="gate",
                    operation="transcription",
                    scope_kind="telegram_connector",
                    scope_ref="telegram_business",
                    period_key=context.original["period_key"],
                    reserved_microusd=3_000_000,
                    settled_microusd=None,
                    state="reserved",
                    error_code=None,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                session.add(held)
                await session.commit()
                held_id = held.id
            assert (await call(factory, world, "322"))["delivered"] == 0
            async with factory() as session:
                assert (
                    await session.get(WatchVoiceNote, context.note_id)
                ).last_error == "billing_hard_limit_exceeded"
                assert (
                    await session.get(WatchVoicePostPaymentClaim, context.note_id)
                ).outcome == "rejected"
                (await session.get(BillingBudgetReservationModel, held_id)).state = "released"
                await session.commit()
            assert (await call(factory, world, "323"))["delivered"] == 0
            assert world.provider_calls == 1 and world.posts == 0
            await assert_history_preserved(factory, context)

    asyncio.run(run())


def test_multipart_post_payment_consumes_claim_without_any_new_stt(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            world.normalizer(monkeypatch, multipart=True)
            await call(factory, world, "322")
            world.normalizer(monkeypatch)
            await call(factory, world, "323")
            async with factory() as session:
                note = await session.get(WatchVoiceNote, context.note_id)
                audit = await session.get(WatchVoicePostPaymentClaim, context.note_id)
                assert note.transcription_state == audit.outcome == "rejected"
                assert note.last_error == "watch_voice_recovery_single_part_required"
            assert world.provider_calls == 1 and world.posts == 0
            await assert_history_preserved(factory, context)

    asyncio.run(run())


def test_claim_crash_and_stale_unknown_never_enter_provider(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)
    original = worker.transcribe_claimed_note

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            monkeypatch.setattr(
                worker, "transcribe_claimed_note", AsyncMock(side_effect=asyncio.CancelledError)
            )
            with pytest.raises(asyncio.CancelledError):
                await call(factory, world, "322")
            monkeypatch.setattr(worker, "transcribe_claimed_note", original)
            async with factory() as session:
                audit = await session.get(WatchVoicePostPaymentClaim, context.note_id)
                assert audit.outcome == "claimed"
                audit.claimed_at = datetime.now(UTC) - timedelta(hours=2)
                await session.commit()
            for run_id in ("323", "324"):
                await call(factory, world, run_id)
            async with factory() as session:
                note = await session.get(WatchVoiceNote, context.note_id)
                audit = await session.get(WatchVoicePostPaymentClaim, context.note_id)
                assert note.transcription_state == audit.outcome == "unknown"
                assert audit.finished_at is not None
            assert world.provider_calls == 1 and world.posts == 0
            await assert_history_preserved(factory, context)

    asyncio.run(run())


def test_crash_after_stored_transcript_finishes_paid_audit_without_second_stt(
    tmp_path, monkeypatch
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)
    original = worker.transcribe_claimed_note

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            monkeypatch.setattr(worker, "transcribe_claimed_note", interrupted)
            with pytest.raises(asyncio.CancelledError):
                await call(factory, world, "322")
            monkeypatch.setattr(worker, "transcribe_claimed_note", original)
            assert (await call(factory, world, "323"))["delivered"] == 1
            async with factory() as session:
                assert (
                    await session.get(WatchVoicePostPaymentClaim, context.note_id)
                ).outcome == "transcribed"
            assert world.provider_calls == 2 and world.posts == 1
            await assert_history_preserved(factory, context)

    asyncio.run(run())


def test_lost_ack_reads_exact_issue_receipt_and_never_reposts_or_retranscribes(
    tmp_path, monkeypatch
):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            world.fail_post = "lost_ack"
            assert (await call(factory, world, "322"))["delivered"] == 0
            recovered = await call(factory, world, "323")
            assert recovered["delivered"] == recovered["reconciled"] == 1
            await call(factory, world, "324")
            assert world.provider_calls == 2 and world.posts == len(world.issues) == 1
            await assert_history_preserved(factory, context)

    asyncio.run(run())


def test_private_paid_receipt_and_archive_access_keep_old_receipt_distinct(tmp_path, monkeypatch):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            await call(factory, world, "322")
            async with factory() as session:
                note = await session.get(WatchVoiceNote, context.note_id)
                receipt = await protected_post_payment_receipt(session, note)
                assert receipt["outcome"] == "transcribed"
                assert (
                    receipt["reservation_state"] == "settled" and receipt["actual_microusd"] == 45
                )
                assert receipt["authorization_kind"] == "owner_post_payment"
                assert receipt["provider_readiness"] == "available_for_attempt"
                assert receipt["finished_at"] and receipt["previous_attempted_at"]
                encoded = json.dumps(receipt)
                for private in (
                    world.transcript,
                    "synthetic-openai",
                    "synthetic-job-token",
                    "synthetic-original-audio",
                ):
                    assert private not in encoded
                assert "quota_restored_at" not in receipt
                from app.api import mcp_routes

                principal = SimpleNamespace(
                    access_scope=SimpleNamespace(
                        workspace_id="mark-personal",
                        project_ids={mcp_routes.UNCLASSIFIED_PROJECT_ID},
                    )
                )
                tool = await mcp_routes._watch_voice_tool_result(
                    request_id="synthetic",
                    selector="watch-voice:" + context.note_id,
                    principal=principal,
                    session=session,
                    settings=action_settings(),
                )
                protected = json.loads(tool["result"]["content"][0]["text"])
                assert protected["post_payment"] == receipt
                assert protected["recovery"]["outcome"] == "rejected"
                principal.access_scope.workspace_id = "foreign"
                denied_session = AsyncMock()
                denied = await mcp_routes._watch_voice_tool_result(
                    request_id="synthetic",
                    selector="watch-voice:" + context.note_id,
                    principal=principal,
                    session=denied_session,
                    settings=action_settings(),
                )
                assert denied["result"]["isError"] is True
                denied_session.scalar.assert_not_called()
                denied_session.get.assert_not_called()
            await assert_history_preserved(factory, context)

    asyncio.run(run())


def test_actual_postgres_concurrent_paid_claim_is_one_new_stt(tmp_path, monkeypatch):
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("actual PostgreSQL DATABASE_URL required for post-payment row-lock concurrency")
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            scoped = await bound_settings(
                action_settings(), world.binding, factory, job_token="synthetic-job-token"
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await asyncio.gather(
                    *(
                        continue_note(
                            scoped, factory, http, world.binding, authorization_sha=claims()["sha"]
                        )
                        for _ in range(6)
                    )
                )
            async with factory() as session:
                assert (
                    await session.get(WatchVoiceNote, context.note_id)
                ).transcription_state == "transcribed"
                assert len((await session.scalars(select(WatchVoicePostPaymentClaim))).all()) == 1
            assert world.provider_calls == 2 and world.posts == 0
            await assert_history_preserved(factory, context)

    asyncio.run(run())


@pytest.mark.parametrize("field", ["public_repo", "foreign_repo"])
def test_private_target_preflight_denial_precedes_the_new_paid_claim(tmp_path, monkeypatch, field):
    world = RecoveryWorld()
    world.normalizer(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            context = await previous_quota(factory, world)
            setattr(world, field, True)
            with pytest.raises(WatchVoiceError):
                await call(factory, world, "322")
            async with factory() as session:
                assert await session.get(WatchVoicePostPaymentClaim, context.note_id) is None
                assert await reservation(session, context.note_id, "postpayment1") is None
            assert world.provider_calls == 1 and world.posts == 0
            await assert_history_preserved(factory, context)

    asyncio.run(run())
