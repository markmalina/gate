"""Synthetic Work/Gate fencing, durable replay and exact same-issue readback."""

import asyncio
import hashlib
import importlib.util
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import Settings
from app.infrastructure.watch_voice_cards import card_body
from app.infrastructure.watch_voice_github import MarkInboxClient, WatchVoiceError, body_digest
from app.infrastructure.watch_voice_status import STATUS_SCHEMA, protected_status
from app.infrastructure.watch_voice_work import (
    accept_work_result,
    claim_work,
    drain_work_receipts,
    expire_work_claims,
    start_work,
    submit_work,
)
from app.models.watch_voice import (
    WatchVoiceDrainRun,
    WatchVoiceNote,
    WatchVoiceSource,
    WatchVoiceTextReceipt,
    WatchVoiceWorkClaim,
    WatchVoiceWorkResult,
)
from app.worker import watch_voice as worker
from jsonschema import validate
from sqlalchemy import create_engine, select, update
from tests.test_watch_voice import World, database, settings
from tests.test_watch_voice_actions import (
    ActionsWorld,
    action_settings,
    claims,
    drain,
)
from tests.test_watch_voice_inventory import note

KEY = str(uuid.UUID(int=1))
TOKEN = "synthetic-nonce-" + "a" * 40
ACTOR = "synthetic-work-actor"
SHA = hashlib.sha256(b"abc").hexdigest()
REV = "abcdef1234"
URL = "https://github.com/markmalina/mark/issues/101"


def cfg(**extra):
    return settings().model_copy(
        update={
            "dropbox_call_account_id": "owner",
            "gate_watch_voice_work_reserve_enabled": True,
            "gate_watch_voice_early_inbox_enabled": True,
            "gate_watch_voice_file_id": "id:synthetic1",
            "gate_watch_voice_revision": REV,
            **extra,
        }
    )


class WorkWorld(World):
    def __init__(self):
        super().__init__()
        self.audio = b"abc"
        self.hide_comments = False

    def handle(self, request):
        if (
            self.hide_comments
            and request.method == "GET"
            and request.url.path.endswith("/comments")
        ):
            return httpx.Response(200, json=[])
        try:
            response = super().handle(request)
        except httpx.ReadTimeout:
            self.comments[-1]["created_at"] = datetime.now(UTC).isoformat()
            raise
        if request.method == "POST" and request.url.path.endswith("/comments"):
            obj = response.json()
            obj["created_at"] = datetime.now(UTC).isoformat()
            obj["user"]["login"] = request.headers.get("X-Test-Writer", "synthetic-writer")
            self.comments[-1] = obj
            return httpx.Response(201, json=obj)
        return response


class WholeSecondWorld(WorkWorld):
    def __init__(self):
        super().__init__()
        self.second = datetime.now(UTC).replace(microsecond=0).isoformat()

    def handle(self, request):
        response = super().handle(request)
        if request.method == "POST" and request.url.path.endswith("/comments"):
            obj = response.json()
            obj["created_at"] = self.second
            self.comments[-1] = obj
            return httpx.Response(201, json=obj)
        return response


@pytest.mark.parametrize("change", ("none", "reverse_api", "swap_bodies", "replace_id"))
def test_equal_second_parts_require_original_comment_identity_and_order(tmp_path, change):
    async def run():
        async with database(tmp_path) as factory:
            world = WholeSecondWorld()
            await seed(factory)
            await prepare(factory, "я" * 12001)
            world.hide_comments = True
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row)
                assert (await current(factory))[1].state == "saved"
                assert len(world.comments) == 2
                assert world.comments[0]["created_at"] == world.comments[1]["created_at"]
                if change == "swap_bodies":
                    first, second = world.comments
                    first["body"], second["body"] = second["body"], first["body"]
                elif change == "replace_id":
                    world.comments[0]["html_url"] = URL + "#issuecomment-999"
                elif change == "reverse_api":
                    world.comments.reverse()
                world.hide_comments = False
                accepted = await accept_work_result(factory, client(http), KEY)
                assert accepted == (change in {"none", "reverse_api"})
                assert world.posts == 2
                result = (await current(factory))[1]
                assert (result.state == "accepted") == accepted

    asyncio.run(run())


@pytest.mark.parametrize("comment_id", (True, 0, -1, 2**63, "1", 2))
def test_comment_receipt_requires_canonical_matching_immutable_id(comment_id):
    mark = MarkInboxClient(None, "synthetic", "synthetic-writer")
    obj = {"id": comment_id, "html_url": URL + "#issuecomment-1", "body": "text",
           "created_at": datetime.now(UTC).isoformat(),
           "user": {"login": "synthetic-writer"}}
    with pytest.raises(ValueError, match="identity"):
        mark.receipt(obj, part=1, issue_number=101, expected_body="text")


@pytest.mark.parametrize("fragment", ("0", "01", "-1", "1/trailing", "1?x=2", "text"))
def test_comment_receipt_rejects_noncanonical_identity_url(fragment):
    mark = MarkInboxClient(None, "synthetic", "synthetic-writer")
    obj = {"html_url": URL + "#issuecomment-" + fragment, "body": "text",
           "created_at": datetime.now(UTC).isoformat(),
           "user": {"login": "synthetic-writer"}}
    with pytest.raises(ValueError, match="identity"):
        mark.receipt(obj, part=1, issue_number=101, expected_body="text")


@pytest.mark.parametrize("corruption", ("missing_id", "reverse_ids", "body_digest"))
def test_damaged_durable_part_identity_never_confirms_accepted_status(tmp_path, corruption):
    async def run():
        async with database(tmp_path) as factory:
            world = WholeSecondWorld()
            await seed(factory)
            await prepare(factory, "я" * 12001)
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row)
            assert (await current(factory))[1].state == "accepted"
            async with factory() as session:
                part = await session.get(WatchVoiceTextReceipt, (KEY, 0))
                if corruption == "missing_id":
                    part.comment_id = None
                elif corruption == "reverse_ids":
                    part.comment_id = 999
                else:
                    part.body_sha256 = "a" * 64
                await session.commit()
            async with factory() as session:
                with pytest.raises(WatchVoiceError, match="publication_conflict"):
                    await protected_status(session, cfg(), KEY)

    asyncio.run(run())


def test_unidentified_saved_receipt_stays_pending_without_repost(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            world = WholeSecondWorld()
            await seed(factory)
            await prepare(factory)
            world.hide_comments = True
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row)
                async with factory() as session:
                    part = await session.get(WatchVoiceTextReceipt, (KEY, 0))
                    part.comment_id = None
                    await session.commit()
                world.hide_comments = False
                for _ in range(2):
                    assert not await accept_work_result(factory, client(http), KEY)
            assert world.posts == 1
            result = (await current(factory))[1]
            assert result.state == "saved" and result.accepted_at is None

    asyncio.run(run())


def test_part_identity_changed_during_actual_readback_fails_final_snapshot(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            world = WholeSecondWorld()
            await seed(factory)
            await prepare(factory)
            world.hide_comments = True
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                mark = client(http)
                row, _ = await current(factory)
                await worker.publish_note(factory, mark, row)
                world.hide_comments = False
                original = mark.reconcile

                async def change_after_read(**kwargs):
                    receipt = await original(**kwargs)
                    assert receipt is not None
                    async with factory() as session:
                        part = await session.get(WatchVoiceTextReceipt, (KEY, 0))
                        part.comment_id += 1
                        await session.commit()
                    return receipt

                mark.reconcile = change_after_read
                with pytest.raises(WatchVoiceError, match="candidate_conflict"):
                    await accept_work_result(factory, mark, KEY)
            assert (await current(factory))[1].accepted_at is None
            assert world.posts == 1

    asyncio.run(run())


def client(http, writer="synthetic-writer"):
    value = MarkInboxClient(http, "synthetic-github", writer)
    value.headers["X-Test-Writer"] = writer
    return value


async def seed(factory, **changes):
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(
            WatchVoiceNote(
                **note(1, sha256=SHA, issue_number=101, issue_url=URL, revision=REV, **changes)
            )
        )
        row = WatchVoiceSource(
            id=KEY,
            workspace_key="mark-personal",
            account_id="owner",
            file_id="id:synthetic1",
            revision=REV,
            filename="synthetic.m4a",
            source_path="/Alpha/Voice/synthetic.m4a",
            size_bytes=3,
            dropbox_modified_at=now,
            detected_at=now,
            archived_at=now,
            state="archived",
            card_state="saved",
            card_issue_number=101,
            card_issue_url=URL,
            card_received_at=now,
            card_writer="synthetic-writer",
        )
        row.card_body = card_body(row)
        row.card_body_sha256 = body_digest(row.card_body)
        session.add(row)
        await session.commit()


async def claim(factory, configuration=None, **extra):
    return await claim_work(
        configuration or cfg(),
        factory,
        KEY,
        actor=ACTOR,
        token=TOKEN,
        revision=REV,
        audio_sha256=SHA,
        **extra,
    )


async def prepare(factory, text="Полный синтетический текст.", configuration=None):
    configuration = configuration or cfg()
    await claim(factory, configuration)
    await start_work(configuration, factory, KEY, actor=ACTOR, token=TOKEN)
    return await submit_work(
        configuration,
        factory,
        KEY,
        actor=ACTOR,
        token=TOKEN,
        revision=REV,
        audio_sha256=SHA,
        text=text,
        transcript_sha256=body_digest(text),
    )


async def current(factory):
    async with factory() as session:
        return await session.get(WatchVoiceNote, KEY), await session.get(WatchVoiceWorkResult, KEY)


JOURNAL_CORRUPTIONS = (
    "epoch", "claim_id", "claim_state", "candidate_text", "candidate_lengths",
    "prepared_time", "started_time", "submitted_time",
)


async def corrupt_candidate(factory, corruption):
    async with factory() as session:
        result = await session.get(WatchVoiceWorkResult, KEY)
        claim = await session.get(WatchVoiceWorkClaim, result.claim_id)
        if corruption == "epoch":
            result.epoch += 1
        elif corruption == "claim_id":
            result.claim_id = str(uuid.uuid4())
        elif corruption == "claim_state":
            claim.state = "unknown"
        elif corruption == "candidate_text":
            result.transcript = "forged candidate, unchanged digest"
        elif corruption == "candidate_lengths":
            result.characters = result.utf8_bytes = 1
        elif corruption == "prepared_time":
            result.prepared_at = None
        elif corruption == "started_time":
            claim.started_at = None
        elif corruption == "submitted_time":
            claim.submitted_at = claim.expires_at + timedelta(seconds=1)
        await session.commit()


@pytest.mark.parametrize("corruption", JOURNAL_CORRUPTIONS)
@pytest.mark.parametrize("phase", ("before_readback", "during_readback", "after_accept"))
def test_candidate_and_claim_corruption_never_yields_trusted_acceptance(
    tmp_path, corruption, phase,
):
    async def run():
        async with database(tmp_path) as factory:
            world = WorkWorld()
            await seed(factory)
            await prepare(factory)
            world.hide_comments = phase != "after_accept"
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                mark = client(http)
                row, _ = await current(factory)
                await worker.publish_note(factory, mark, row)
                assert (await current(factory))[1].state == (
                    "accepted" if phase == "after_accept" else "saved"
                )
                world.hide_comments = False
                if phase == "during_readback":
                    reconcile = mark.reconcile

                    async def mutate_after_actual_read(**kwargs):
                        receipt = await reconcile(**kwargs)
                        assert receipt is not None
                        await corrupt_candidate(factory, corruption)
                        return receipt

                    mark.reconcile = mutate_after_actual_read
                else:
                    await corrupt_candidate(factory, corruption)
                if phase != "after_accept":
                    try:
                        accepted = await accept_work_result(factory, mark, KEY)
                    except WatchVoiceError as exc:
                        assert exc.code == "watch_voice_work_candidate_conflict"
                    else:
                        assert not accepted
                    result = (await current(factory))[1]
                    assert result.state == "saved" and result.accepted_at is None
                async with factory() as session:
                    with pytest.raises(WatchVoiceError, match="candidate_conflict"):
                        await protected_status(session, cfg(), KEY)

    asyncio.run(run())


@pytest.mark.parametrize("corruption", JOURNAL_CORRUPTIONS)
def test_candidate_corruption_is_rejected_before_any_publication_post(tmp_path, corruption):
    async def run():
        async with database(tmp_path) as factory:
            world = WorkWorld()
            await seed(factory)
            await prepare(factory)
            await corrupt_candidate(factory, corruption)
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                with pytest.raises(WatchVoiceError, match="candidate_conflict"):
                    await worker.publish_note(factory, client(http), row)
            assert not world.comments
            assert (await current(factory))[0].next_part == 0

    asyncio.run(run())


def test_submitted_candidate_recovers_after_lease_expiry_without_new_authority(
    tmp_path, monkeypatch,
):
    from app.infrastructure import watch_voice_work as work

    async def run():
        async with database(tmp_path) as factory:
            world = WorkWorld()
            await seed(factory)
            await prepare(factory)
            async with factory() as session:
                result = await session.get(WatchVoiceWorkResult, KEY)
                claim_row = await session.get(WatchVoiceWorkClaim, result.claim_id)
                future = claim_row.expires_at.replace(tzinfo=UTC) + timedelta(seconds=1)

            class Later(datetime):
                @classmethod
                def now(cls, tz=None):
                    return future if tz else future.replace(tzinfo=None)

            monkeypatch.setattr(work, "datetime", Later)
            await expire_work_claims(cfg(), factory)
            with pytest.raises(WatchVoiceError, match="work_busy"):
                await claim_work(cfg(), factory, KEY, actor=ACTOR, token="b" * 48,
                                 revision=REV, audio_sha256=SHA)
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row)
            result = (await current(factory))[1]
            assert result.state == "accepted"
            assert result.epoch == 1
            assert len(world.comments) == 1
            replay = await submit_work(
                cfg(), factory, KEY, actor=ACTOR, token=TOKEN, revision=REV,
                audio_sha256=SHA, text=result.transcript,
                transcript_sha256=result.transcript_sha256,
            )
            assert replay["provider_authority"] is False
            assert replay["start_recorded_now"] is False

    asyncio.run(run())


def test_default_off_denies_before_database_and_stt_authority_is_absent():
    factory = AsyncMock()
    with pytest.raises(WatchVoiceError, match="work_disabled"):
        asyncio.run(
            claim_work(
                Settings(), factory, KEY, actor=ACTOR, token=TOKEN, revision=REV, audio_sha256=SHA
            )
        )
    factory.assert_not_called()


def test_claim_start_submit_replay_never_creates_another_epoch_or_overwrites(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            first, replay = await claim(factory), await claim(factory)
            assert first == replay and first["epoch"] == 1
            assert first["provider_authority"] is False and TOKEN not in json.dumps(first)
            started = await start_work(cfg(), factory, KEY, actor=ACTOR, token=TOKEN)
            repeated = await start_work(cfg(), factory, KEY, actor=ACTOR, token=TOKEN)
            assert started["start_recorded_now"] and not repeated["start_recorded_now"]
            assert {**started, "start_recorded_now": False} == repeated
            text = "  Сохранить весь текст без нормализации.\n🙂\n"
            args = dict(
                actor=ACTOR,
                token=TOKEN,
                revision=REV,
                audio_sha256=SHA,
                text=text,
                transcript_sha256=body_digest(text),
            )
            prepared = await submit_work(cfg(), factory, KEY, **args)
            assert await submit_work(cfg(), factory, KEY, **args) == prepared
            assert prepared["state"] == "prepared" and prepared["accepted_at"] is None
            assert prepared["characters"] == len(text)
            with pytest.raises(WatchVoiceError, match="candidate_immutable"):
                await submit_work(
                    cfg(),
                    factory,
                    KEY,
                    **{**args, "text": "другой", "transcript_sha256": body_digest("другой")},
                )
            row, result = await current(factory)
            assert row.transcript == result.transcript == text
            assert row.transcript_origin == "work" and row.model is None
            async with factory() as session:
                claims = list((await session.scalars(select(WatchVoiceWorkClaim))).all())
            assert len(claims) == 1
            assert claims[0].token_sha256 == body_digest(TOKEN)
            assert claims[0].actor_sha256 == body_digest(ACTOR)

    asyncio.run(run())


@pytest.mark.parametrize("change", [dict(actor="other-actor"), dict(token="b" * 43)])
def test_actor_and_token_cannot_steal_current_claim(tmp_path, change):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            await claim(factory)
            with pytest.raises(WatchVoiceError, match="stale_or_denied"):
                await start_work(
                    cfg(), factory, KEY, **{**dict(actor=ACTOR, token=TOKEN), **change}
                )
            row, result = await current(factory)
            assert result.state == "claimed" and row.transcript is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "scope",
    [
        dict(dropbox_call_account_id="foreign"),
        dict(gate_workspace_id="foreign", gate_watch_voice_workspace_key="foreign"),
    ],
)
def test_foreign_scope_cannot_claim_an_owner_recording(tmp_path, scope):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            with pytest.raises(WatchVoiceError, match="source_not_found"):
                await claim(factory, cfg(**scope))
            assert (await current(factory))[1] is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("revision", "other-revision", "source_conflict"),
        ("audio_sha256", "0" * 64, "source_conflict"),
    ],
)
def test_claim_is_pinned_to_revision_and_audio_hash(tmp_path, field, value, error):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            with pytest.raises(WatchVoiceError, match=error):
                await claim_work(
                    cfg(),
                    factory,
                    KEY,
                    **{
                        **dict(actor=ACTOR, token=TOKEN, revision=REV, audio_sha256=SHA),
                        field: value,
                    },
                )
            assert (await current(factory))[1] is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        dict(transcription_state="processing"),
        dict(transcription_state="unknown"),
        dict(transcription_state="rejected"),
        dict(processing_attempt_count=1),
        dict(provider_request_count=1),
    ],
)
def test_work_never_takes_over_started_or_unknown_gate_attempt(tmp_path, changes):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory, **changes)
            with pytest.raises(WatchVoiceError, match="work_busy"):
                await claim(factory)
            assert (await current(factory))[1] is None

    asyncio.run(run())


def test_expired_unstarted_claim_releases_without_reusing_old_token(tmp_path):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            original = await claim(factory)
            async with factory() as session:
                await session.execute(
                    update(WatchVoiceWorkClaim).values(
                        expires_at=datetime.now(UTC) - timedelta(seconds=1)
                    )
                )
                await session.commit()
            await expire_work_claims(cfg(gate_watch_voice_work_reserve_enabled=False), factory)
            row, result = await current(factory)
            assert row.transcription_state == "received" and result.state == "idle"
            with pytest.raises(WatchVoiceError, match="stale_or_denied"):
                await start_work(cfg(), factory, KEY, actor=ACTOR, token=TOKEN)
            replacement = await claim_work(
                cfg(), factory, KEY, actor=ACTOR, token="b" * 43, revision=REV, audio_sha256=SHA
            )
            assert replacement["epoch"] == original["epoch"] + 1
            async with factory() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(WatchVoiceWorkClaim).order_by(WatchVoiceWorkClaim.epoch)
                        )
                    ).all()
                )
            assert [x.state for x in rows] == ["expired", "claimed"]

    asyncio.run(run())


@pytest.mark.parametrize("started", [False, True])
def test_new_claim_reconciles_expiry_without_a_running_driver(tmp_path, started):
    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            await claim(factory)
            if started:
                await start_work(cfg(), factory, KEY, actor=ACTOR, token=TOKEN)
            async with factory() as session:
                await session.execute(
                    update(WatchVoiceWorkClaim).values(
                        expires_at=datetime.now(UTC) - timedelta(seconds=1)
                    )
                )
                await session.commit()
            arguments = dict(actor=ACTOR, token="b" * 43, revision=REV, audio_sha256=SHA)
            if started:
                with pytest.raises(WatchVoiceError, match="work_busy"):
                    await claim_work(cfg(), factory, KEY, **arguments)
                row, result = await current(factory)
                assert row.transcription_state == result.state == "unknown"
            else:
                receipt = await claim_work(cfg(), factory, KEY, **arguments)
                assert receipt["epoch"] == 2

    asyncio.run(run())


def test_expired_started_claim_is_unknown_and_never_dispatches_gate(tmp_path, monkeypatch):
    world = WorkWorld()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            await claim(factory)
            await start_work(cfg(), factory, KEY, actor=ACTOR, token=TOKEN)
            async with factory() as session:
                await session.execute(
                    update(WatchVoiceWorkClaim).values(
                        expires_at=datetime.now(UTC) - timedelta(seconds=1)
                    )
                )
                await session.commit()
            await expire_work_claims(cfg(gate_watch_voice_work_reserve_enabled=False), factory)
            row, result = await current(factory)
            assert row.transcription_state == result.state == "unknown"
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.transcribe_note(cfg(), factory, http, KEY)
            assert world.provider_calls == 0
            with pytest.raises(WatchVoiceError, match="work_busy"):
                await claim_work(
                    cfg(), factory, KEY, actor=ACTOR, token="b" * 43, revision=REV, audio_sha256=SHA
                )

    asyncio.run(run())


def test_multipart_same_issue_and_serial_writer_switch_accepts_only_after_readback(tmp_path):
    world = WorkWorld()

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            text = "я" * 12000 + "🙂" * 6001
            await prepare(factory, text)
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                for index, writer in enumerate(["synthetic-writer", "writer-b", "writer-b"]):
                    row, _ = await current(factory)
                    await worker.publish_note(factory, client(http, writer), row, max_parts=1)
                    _, result = await current(factory)
                    assert result.state == ("accepted" if index == 2 else "prepared")
            row, result = await current(factory)
            assert row.issue_number == 101 and row.next_part == 3
            assert result.accepted_at and result.transcript == text
            async with factory() as session:
                parts = list(
                    (
                        await session.scalars(
                            select(WatchVoiceTextReceipt).order_by(WatchVoiceTextReceipt.part)
                        )
                    ).all()
                )
                status = await protected_status(session, cfg(), KEY)
            assert [p.writer for p in parts] == ["synthetic-writer", "writer-b", "writer-b"]
            assert world.posts == 3 and not world.issues
            validate(status, STATUS_SCHEMA)
            assert status["coordination"]["accepted_result"]["sha256"] == body_digest(text)
            assert "Расшифровка, часть 1/3" in world.comments[0]["body"]
            assert "Происхождение текста: Work" in world.comments[0]["body"]
            assert not status["publication"]["external_content_verified"]

    asyncio.run(run())


def test_lost_post_ack_reconciles_without_reposting(tmp_path):
    world = WorkWorld()

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            await prepare(factory)
            world.fail_post = "lost_ack"
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row)
                row, result = await current(factory)
                assert row.delivery_state == "unknown" and result.state == "prepared"
                assert world.posts == 1
                await worker.publish_note(factory, client(http, "writer-b"), row)
            row, result = await current(factory)
            assert result.state == "accepted" and row.next_part == 1
            assert world.posts == 1 and len(world.comments) == 1

    asyncio.run(run())


def test_full_text_saved_with_missing_readback_stays_saved_then_recovers(tmp_path):
    world = WorkWorld()

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            await prepare(factory)
            world.hide_comments = True
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row)
                row, result = await current(factory)
                assert row.delivery_state == "delivered" and result.state == "saved"
                assert result.accepted_at is None
                world.hide_comments = False
                await drain_work_receipts(
                    cfg(gate_watch_voice_work_reserve_enabled=False), factory, client(http)
                )
            assert (await current(factory))[1].state == "accepted"
            assert world.posts == 1

    asyncio.run(run())


@pytest.mark.parametrize("corruption", ["writer", "body", "duplicate", "receipt_missing", "time"])
def test_forged_partial_or_duplicate_readback_cannot_be_accepted(tmp_path, corruption):
    world = WorkWorld()

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            await prepare(factory)
            world.hide_comments = True
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                row, _ = await current(factory)
                await worker.publish_note(factory, client(http), row)
                world.hide_comments = False
                if corruption == "writer":
                    world.comments[0]["user"]["login"] = "forged-writer"
                elif corruption == "body":
                    world.comments[0]["body"] += "изменено"
                elif corruption == "duplicate":
                    world.comments.append(dict(world.comments[0]))
                elif corruption == "time":
                    world.comments[0]["created_at"] = (
                        datetime.now(UTC) + timedelta(days=1)
                    ).isoformat()
                else:
                    async with factory() as session:
                        part = await session.get(WatchVoiceTextReceipt, (KEY, 0))
                        await session.delete(part)
                        await session.commit()
                assert not await accept_work_result(factory, client(http), KEY)
            assert (await current(factory))[1].accepted_at is None
            assert world.posts == 1

    asyncio.run(run())


def test_tampered_selected_text_is_rejected_before_any_post(tmp_path):
    world = WorkWorld()

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            await prepare(factory)
            async with factory() as session:
                await session.execute(
                    update(WatchVoiceNote)
                    .where(WatchVoiceNote.id == KEY)
                    .values(transcript="tampered")
                )
                await session.commit()
            row, _ = await current(factory)
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                with pytest.raises(WatchVoiceError, match="candidate_conflict"):
                    await worker.publish_note(factory, client(http), row)
            assert not world.posts and not world.comments

    asyncio.run(run())


class CoreActionsWorld(ActionsWorld):
    def __init__(self):
        super().__init__()
        self.binding.update(file_id="id:synthetic1", revision=REV)
        self.hide_comments = False

    def handle(self, request):
        if request.url.path.endswith("/users/get_current_account"):
            return httpx.Response(200, json={"account_id": "owner"})
        if request.url.path.endswith(("/files/list_folder", "/files/list_folder/continue")):
            return httpx.Response(
                200, json={"entries": [], "cursor": "synthetic", "has_more": False}
            )
        if (
            self.hide_comments
            and request.method == "GET"
            and request.url.path.endswith("/comments")
        ):
            return httpx.Response(200, json=[])
        response = super().handle(request)
        if request.method == "POST" and request.url.path.endswith("/comments"):
            obj = response.json()
            obj["created_at"] = datetime.now(UTC).isoformat()
            self.comments[-1] = obj
            return httpx.Response(201, json=obj)
        return response


def test_actions_delivers_one_work_text_part_per_run_then_retries_readback_only(tmp_path):
    world = CoreActionsWorld()

    async def run():
        async with database(tmp_path) as factory:
            async with factory() as session:
                await session.run_sync(
                    lambda s: WatchVoiceDrainRun.__table__.create(s.connection())
                )
                await session.commit()
            await seed(factory)
            await prepare(factory, "я" * 12000 + "🙂" * 6001)
            world.hide_comments = True
            configuration = action_settings().model_copy(
                update={
                    "dropbox_call_account_id": "owner",
                    "gate_watch_voice_early_inbox_enabled": True,
                    "gate_watch_voice_work_reserve_enabled": True,
                }
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                for index in range(3):
                    world.current_run = str(321 + index)
                    await drain(
                        settings=configuration,
                        factory=factory,
                        http=http,
                        claims=claims(run_id=world.current_run, jti=f"synthetic-{index}"),
                        job_token="synthetic-job-token",
                    )
                    assert world.posts == index + 1
                assert (await current(factory))[1].state == "saved"
                world.hide_comments = False
                world.current_run = "324"
                await drain(
                    settings=configuration,
                    factory=factory,
                    http=http,
                    claims=claims(run_id="324", jti="synthetic-readback"),
                    job_token="synthetic-job-token",
                )
            assert (await current(factory))[1].state == "accepted"
            assert world.posts == 3 and world.provider_calls == 0

    asyncio.run(run())


def test_persisted_result_barrier_survives_admission_off_and_note_state_reset(
    tmp_path, monkeypatch
):
    world = WorkWorld()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            await prepare(factory)
            async with factory() as session:
                await session.execute(
                    update(WatchVoiceNote)
                    .where(WatchVoiceNote.id == KEY)
                    .values(transcription_state="received")
                )
                await session.commit()
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.transcribe_note(
                    cfg(gate_watch_voice_work_reserve_enabled=False), factory, http, KEY
                )
            assert world.provider_calls == 0
            assert (await current(factory))[1].state == "prepared"

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="requires real PostgreSQL row locks")
@pytest.mark.parametrize("round_index", range(4))
def test_real_postgres_gate_and_eight_work_claims_have_one_winner(
    tmp_path, monkeypatch, round_index
):
    world = WorkWorld()
    world.patches(monkeypatch)

    async def run():
        async with database(tmp_path) as factory:
            await seed(factory)
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                calls = [
                    claim_work(
                        cfg(),
                        factory,
                        KEY,
                        actor=ACTOR,
                        token=f"nonce-{i}-" + "a" * 40,
                        revision=REV,
                        audio_sha256=SHA,
                    )
                    for i in range(8)
                ]
                gate = worker.transcribe_note(cfg(), factory, http, KEY)
                results = await asyncio.gather(
                    *(calls + [gate] if round_index % 2 else [gate] + calls), return_exceptions=True
                )
            work_winners = [r for r in results if isinstance(r, dict)]
            assert len(work_winners) + world.provider_calls == 1
            assert all(
                not isinstance(r, Exception) or isinstance(r, WatchVoiceError) for r in results
            )
            row, result = await current(factory)
            assert row.data == b"abc" and row.issue_number == 101
            assert result is None or result.epoch == 1

    asyncio.run(run())


def test_additive_work_migration_preserves_originals_and_refuses_journal_loss():
    path = Path("alembic/versions/0118_watch_voice_work_results.py")
    spec = importlib.util.spec_from_file_location("work_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0117_watch_voice_early_inbox"
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE watch_voice_notes (id VARCHAR(36) PRIMARY KEY,data BLOB,transcript TEXT)"
        )
        conn.exec_driver_sql(
            "INSERT INTO watch_voice_notes VALUES ('old',?,?)",
            (b"original\x00bytes", "полный текст"),
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.exec_driver_sql(
            "SELECT data,transcript,transcript_origin FROM watch_voice_notes"
        ).one() == (b"original\x00bytes", "полный текст", "gate")
        conn.exec_driver_sql(
            "INSERT INTO watch_voice_work_results "
            "(note_id,revision,audio_sha256,issue_number,issue_url) VALUES ('old','rev',?,101,?)",
            ("0" * 64, URL),
        )
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="would_lose_journal"):
                migration.downgrade()
        conn.exec_driver_sql("DELETE FROM watch_voice_work_results")
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert conn.exec_driver_sql("SELECT data,transcript FROM watch_voice_notes").one() == (
            b"original\x00bytes",
            "полный текст",
        )


def test_comment_identity_migration_preserves_unidentified_receipts_and_refuses_identity_loss():
    path = Path("alembic/versions/0119_watch_voice_comment_ids.py")
    spec = importlib.util.spec_from_file_location("identity_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0118_watch_voice_work_results"
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE watch_voice_text_receipts "
            "(note_id VARCHAR(36),part INTEGER,body_sha256 VARCHAR(64),"
            "PRIMARY KEY(note_id,part))"
        )
        conn.exec_driver_sql(
            "INSERT INTO watch_voice_text_receipts VALUES ('old',0,?)", ("b" * 64,)
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.exec_driver_sql(
            "SELECT body_sha256,comment_id FROM watch_voice_text_receipts"
        ).one() == ("b" * 64, None)
        conn.exec_driver_sql("UPDATE watch_voice_text_receipts SET comment_id=6493515845")
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="would_lose_identity"):
                migration.downgrade()
        assert conn.exec_driver_sql(
            "SELECT comment_id FROM watch_voice_text_receipts"
        ).scalar_one() == 6493515845
        conn.exec_driver_sql("UPDATE watch_voice_text_receipts SET comment_id=NULL")
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert conn.exec_driver_sql(
            "SELECT body_sha256 FROM watch_voice_text_receipts"
        ).scalar_one() == "b" * 64
