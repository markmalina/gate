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
                settings, factory, AppInboxClient(http, "fixture", app_slug="writer"), SCOPE
            )
        async with factory.begin() as session:
            card = await session.get(WatchEarlyCard, event_id)
            assert card.mark_published_version == 0
            assert card.mark_update_error == "github_watch_patch_unknown"
            assert not await has_mark_updates(session, SCOPE)
            card.mark_update_retry_at = None

        async with httpx.AsyncClient(transport=transport) as http:
            client = AppInboxClient(http, "fixture", app_slug="writer")
            assert await publish_mark_update(settings, factory, client, SCOPE)
            assert patch_versions == ["<!-- version=2 state=starting -->"]
            assert not await publish_mark_update(settings, factory, client, SCOPE)
            async with factory.begin() as session:
                await session.execute(update(WatchVoiceNote).values(
                    recognition_started_at=now, mark_publish_version=next_mark_publish_version(),
                ))
                await session.execute(update(WatchVoiceNote).values(
                    transcription_state="transcribed", transcript="synthetic",
                    text_saved_at=now, mark_publish_version=next_mark_publish_version(),
                ))
            assert await publish_mark_update(settings, factory, client, SCOPE)
            assert patch_versions == [
                "<!-- version=2 state=starting -->", "<!-- version=4 state=transcribed -->",
            ]
            assert "расшифровка готова" in issue_body and now.isoformat() in issue_body
            assert issue_body.endswith("Текст владельца.")
            async with factory.begin() as session:
                await session.execute(update(WatchVoiceNote).values(
                    transcription_state="unknown",
                    issue_url=ISSUE_URL.replace("42", "43"),
                    mark_publish_version=next_mark_publish_version(),
                ))
            assert await publish_mark_update(settings, factory, client, SCOPE)
            async with factory() as session:
                card = await session.get(WatchEarlyCard, event_id)
                assert card.mark_published_version == 4
                assert card.mark_update_blocked
                assert card.mark_update_error == "watch_mark_source_mismatch"
            assert len(patch_versions) == 2
    finally:
        await engine.dispose()
