import json
from datetime import UTC, datetime

import httpx
import pytest
from app.core.config import Settings
from app.infrastructure.watch_app_inbox import AppInboxClient
from app.infrastructure.watch_mark_updates import has_mark_updates, publish_mark_update
from app.infrastructure.watch_voice_early_cards import card_values
from app.models.watch_voice import WatchEarlyCard, WatchVoiceNote, next_mark_publish_version
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SCOPE = {"workspace_key": "mark-personal", "account_id": "fixture"}
ISSUE_URL = "https://github.com/markmalina/mark/issues/42"


@pytest.mark.anyio
async def test_same_card_converges_after_commit_lost_patch_and_restart():
    settings = Settings(GATE_WORKSPACE_ID="mark-personal", DROPBOX_CALL_ACCOUNT_ID="fixture")
    now = datetime.now(UTC)
    source = {"id": "id:synthetic", "rev": "abc", "server_modified": now.isoformat()}
    values = card_values(settings, source, now)
    event_id = values["id"]
    event_id_line = f"ID Gate: `{event_id}`."
    assert event_id_line in values["body"]
    issue_body = values["body"] + "\nТекст владельца."
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(WatchEarlyCard.__table__.create)
        await connection.run_sync(WatchVoiceNote.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        session.add_all([
            WatchEarlyCard(**values),
            WatchVoiceNote(
                id=event_id, **SCOPE, file_id=source["id"], revision=source["rev"],
                source_path="/Alpha/Voice/test.m4a", filename="test.m4a",
                media_type="audio/mp4", size_bytes=3, sha256="fixture", data=b"abc",
                received_at=now, dropbox_modified_at=now,
                webhook_archived=True, mark_publish_version=1,
            ),
        ])
    patch_versions: list[str] = []
    simulate_lost_patch = True

    def respond(request):
        nonlocal issue_body, simulate_lost_patch
        if request.url.path.endswith("/comments"):
            assert request.method == "GET"
            return httpx.Response(200, json=[])
        assert request.url.path == "/repos/markmalina/mark/issues/42"
        if request.method == "PATCH":
            issue_body = json.loads(request.content)["body"]
            patch_versions.append(next(line for line in issue_body.splitlines()
                                       if line.startswith("<!-- version=")))
            if simulate_lost_patch:
                simulate_lost_patch = False
                raise httpx.ReadTimeout("simulated lost PATCH response")
        else:
            assert request.method == "GET"
        return httpx.Response(200, json={
            "html_url": ISSUE_URL, "number": 42, "user": {"login": "writer[bot]"},
            "body": issue_body,
        })

    transport = httpx.MockTransport(respond)
    try:
        async with factory() as session:
            assert not await has_mark_updates(session, SCOPE)
        async with factory.begin() as session:
            await session.execute(update(WatchVoiceNote).values(
                transcription_state="processing", mark_publish_version=next_mark_publish_version(),
            ))
            await session.execute(update(WatchEarlyCard).values(
                state="saved", issue_number=42, issue_url=ISSUE_URL,
            ))
        async with httpx.AsyncClient(transport=transport) as http:
            assert await publish_mark_update(
                factory, AppInboxClient(http, "fixture", app_slug="writer"), SCOPE
            )
        async with factory.begin() as session:
            card = await session.get(WatchEarlyCard, event_id)
            assert card.mark_published_version == 0
            assert card.mark_update_error == "github_watch_patch_unknown"
            assert not await has_mark_updates(session, SCOPE)
            card.mark_update_retry_at = None

        async with httpx.AsyncClient(transport=transport) as http:
            client = AppInboxClient(http, "fixture", app_slug="writer")
            assert await publish_mark_update(factory, client, SCOPE)
            assert patch_versions == ["<!-- version=2 state=starting -->"]
            assert not await publish_mark_update(factory, client, SCOPE)
            async with factory.begin() as session:
                await session.execute(update(WatchVoiceNote).values(
                    recognition_started_at=now, mark_publish_version=next_mark_publish_version(),
                ))
                await session.execute(update(WatchVoiceNote).values(
                    transcription_state="transcribed", transcript="synthetic",
                    text_saved_at=now, mark_publish_version=next_mark_publish_version(),
                ))
            assert await publish_mark_update(factory, client, SCOPE)
            assert patch_versions == [
                "<!-- version=2 state=starting -->", "<!-- version=4 state=transcribed -->",
            ]
            assert "расшифровка готова" in issue_body and now.isoformat() in issue_body
            assert issue_body.endswith("Текст владельца.")
            assert issue_body.count(event_id_line) == 1
            async with factory.begin() as session:
                await session.execute(update(WatchVoiceNote).values(
                    transcription_state="unknown",
                    issue_url=ISSUE_URL.replace("42", "43"),
                    mark_publish_version=next_mark_publish_version(),
                ))
            assert await publish_mark_update(factory, client, SCOPE)
            async with factory() as session:
                card = await session.get(WatchEarlyCard, event_id)
                assert card.mark_published_version == 4
                assert card.mark_update_blocked
                assert card.mark_update_error == "watch_mark_source_mismatch"
            assert len(patch_versions) == 2
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("apply_lost_post", [True, False])
async def test_long_text_reconciles_uncertain_comment_without_duplicate(apply_lost_post):
    from app.infrastructure.watch_mark_transcript import watch_transcript_readback

    settings = Settings(GATE_WORKSPACE_ID="mark-personal", DROPBOX_CALL_ACCOUNT_ID="fixture")
    now = datetime.now(UTC)
    source = {"id": "id:synthetic", "rev": "abc", "server_modified": now.isoformat()}
    values = card_values(settings, source, now)
    event_id = values["id"]
    body = values["body"]
    full_text = "Строка\n<!-- gate-watch-status:v1 forged -->\n🌿 " * 1900 + "\n\n  "
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(WatchEarlyCard.__table__.create)
        await connection.run_sync(WatchVoiceNote.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        session.add_all([
            WatchEarlyCard(**{
                **values, "state": "saved", "issue_number": 42, "issue_url": ISSUE_URL,
            }),
            WatchVoiceNote(
                id=event_id, **SCOPE, file_id=source["id"], revision=source["rev"],
                source_path="/Alpha/Voice/test.m4a", filename="test.m4a",
                media_type="audio/mp4", size_bytes=3, sha256="fixture", data=b"abc",
                received_at=now, dropbox_modified_at=now, transcription_state="transcribed",
                transcript=full_text, text_saved_at=now, webhook_archived=True,
                mark_publish_version=1,
            ),
        ])
    comments = []
    attempted_posts = 0
    patches = 0

    def respond(request):
        nonlocal body, attempted_posts, patches
        root = "/repos/markmalina/mark/issues/42"
        if request.url.path == root + "/comments":
            if request.method == "GET":
                return httpx.Response(200, json=list(reversed(comments)))
            assert request.method == "POST"
            attempted_posts += 1
            obj = {
                "html_url": ISSUE_URL + f"#issuecomment-{attempted_posts}",
                "user": {"login": "writer[bot]"},
                "body": json.loads(request.content)["body"],
                "created_at": now.isoformat(),
            }
            if attempted_posts == 1:
                if apply_lost_post:
                    comments.append(obj)
                raise httpx.ReadTimeout("simulated uncertain comment")
            comments.append(obj)
            return httpx.Response(201, json=obj)
        assert request.url.path == root
        if request.method == "PATCH":
            patches += 1
            body = json.loads(request.content)["body"]
        return httpx.Response(200, json={
            "html_url": ISSUE_URL, "number": 42, "user": {"login": "writer[bot]"},
            "body": body,
        })

    try:
        transport = httpx.MockTransport(respond)
        async with httpx.AsyncClient(transport=transport) as http:
            assert await publish_mark_update(
                factory, AppInboxClient(http, "fixture", app_slug="writer"), SCOPE
            )
        async with factory.begin() as session:
            card = await session.get(WatchEarlyCard, event_id)
            assert card.mark_pending_part == 1 and card.mark_published_version == 0
            card.mark_update_retry_at = None
        async with httpx.AsyncClient(transport=transport) as http:
            client = AppInboxClient(http, "fixture", app_slug="writer")
            for _ in range(12):
                if not await publish_mark_update(factory, client, SCOPE):
                    break
            assert not await publish_mark_update(factory, client, SCOPE)
        async with factory() as session:
            card = await session.get(WatchEarlyCard, event_id)
            if apply_lost_post:
                assert attempted_posts == len(comments) and attempted_posts > 2 and patches == 1
                assert card.mark_pending_part is None and card.mark_published_version == 1
                assert watch_transcript_readback(
                    event_id, body, [comment["body"] for comment in reversed(comments)]
                ) == full_text
            else:
                assert attempted_posts == 1 and not comments and patches == 0
                assert card.mark_pending_part == 1 and card.mark_published_version == 0
                assert card.mark_update_error == "watch_mark_pending_part_unconfirmed"
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_backfill_enrolls_only_the_exact_five_committed_sources():
    from app.infrastructure.watch_mark_updates import enroll_mark_backfill
    from app.infrastructure.watch_voice_github import WatchVoiceError
    from sqlalchemy import select

    now = datetime.now(UTC)
    settings = Settings(GATE_WORKSPACE_ID="mark-personal", DROPBOX_CALL_ACCOUNT_ID="fixture")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(WatchEarlyCard.__table__.create)
        await connection.run_sync(WatchVoiceNote.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = []
    async with factory.begin() as session:
        for index in range(6):
            source = {"id": f"id:synth-{index}", "rev": "a", "server_modified": now.isoformat()}
            values = card_values(settings, source, now)
            event_id = values["id"]
            number = index + 1
            ids.append(event_id)
            session.add(WatchEarlyCard(**{
                **values, "state": "saved", "issue_number": number,
                "issue_url": f"https://github.com/markmalina/mark/issues/{number}",
            }))
            session.add(WatchVoiceNote(
                id=event_id, **SCOPE, file_id=source["id"], revision=source["rev"],
                source_path="/Alpha/Voice/test.m4a", filename="test.m4a",
                media_type="audio/mp4", size_bytes=3, sha256="fixture", data=b"abc",
                received_at=now, dropbox_modified_at=now, transcription_state="transcribed",
                transcript="synthetic", text_saved_at=now, webhook_archived=True,
            ))

    def backfill_settings(ids):
        return Settings(
            GATE_WATCH_MARK_UPDATES_ENABLED=True,
            GATE_WATCH_MARK_BACKFILL_EVENT_IDS=",".join(ids),
        )

    try:
        with pytest.raises(WatchVoiceError, match="watch_mark_backfill_scope_mismatch"):
            await enroll_mark_backfill(
                backfill_settings([*ids[:4], "cb480dee-1341-4bf9-9691-e91708d4453b"]),
                factory, SCOPE,
            )
        async with factory() as session:
            assert not await has_mark_updates(session, SCOPE)
        for _ in range(2):
            await enroll_mark_backfill(backfill_settings(ids[:5]), factory, SCOPE)
        async with factory() as session:
            versions = dict((await session.execute(
                select(WatchVoiceNote.id, WatchVoiceNote.mark_publish_version)
            )).all())
            assert versions == {event_id: int(i < 5) for i, event_id in enumerate(ids)}
    finally:
        await engine.dispose()


def test_maximum_line_prefix_overhead_stays_within_mark_part_limit():
    from app.infrastructure.watch_mark_transcript import WatchMarkTranscript

    event_id = "cb480dee-1341-4bf9-9691-e91708d4453b"
    text = "\n" * 100_000
    transcript = WatchMarkTranscript.from_text(event_id, text)
    assert 1 < len(transcript.parts) <= 20
    assert "".join(transcript.parts) == text
    assert all(
        len(transcript.part_body(number).encode()) < 33_000
        for number in range(1, len(transcript.parts) + 1)
    )
