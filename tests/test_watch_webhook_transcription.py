import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from app.core.config import Settings
from app.infrastructure.watch_app_auth import WatchAppInstallation
from app.infrastructure.watch_voice_github import API, REPOSITORY, marker, transcript_parts
from app.models.watch_voice import WatchEarlyCard, WatchEarlyCursor, WatchVoiceNote
from app.worker import watch_voice
from app.worker import watch_webhook_transcription as worker
from pydantic import SecretStr
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

CANARY = "1ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
FUTURE = "2ac6d623-d0a6-47f7-a358-935ebf2fd5ad"
HISTORY = "3ac6d623-d0a6-47f7-a358-935ebf2fd5ad"


def test_watch_transcription_claim_and_exact_same_issue_with_lost_ack(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        @event.listens_for(engine.sync_engine, "connect")
        def register_lock(connection, _):
            connection.create_function("pg_try_advisory_xact_lock", 1, lambda _: True)

        async with engine.begin() as conn:
            for model in (WatchEarlyCursor, WatchEarlyCard, WatchVoiceNote):
                await conn.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(UTC)
        cfg = Settings(
            GATE_WORKSPACE_ID="mark-personal",
            DROPBOX_CALL_ACCOUNT_ID="owner",
            GATE_WATCH_WEBHOOK_ENABLED=True,
            GATE_WATCH_WEBHOOK_ARCHIVE_ENABLED=True,
            GATE_WATCH_VOICE_DATA_GATE_APPROVED=True,
            OPENAI_API_KEY=SecretStr("synthetic"),
            GATE_WATCH_WEBHOOK_TRANSCRIPTION_ADMISSION=SecretStr(f"canary:{CANARY}"),
        )
        assert worker.watch_transcription_admission(cfg) == ("canary", CANARY)
        assert worker.watch_transcription_admission(
            cfg.model_copy(update={"gate_watch_voice_enabled": True})
        ) is None
        scope = {"workspace_key": "mark-personal", "account_id": "owner"}
        notes = {7: CANARY, 8: FUTURE, 9: HISTORY}
        second_card_body = "synthetic card temporarily changed"

        async with factory.begin() as session:
            session.add(WatchEarlyCursor(**scope, writer_mode="app_webhook", cutover_at=now))
            for number, note_id in notes.items():
                at = now + timedelta(seconds={7: 0, 8: 1, 9: -1}[number])
                source = dict(**scope, id=note_id, file_id=f"id:{number}", revision="abcd")
                session.add(WatchVoiceNote(
                    **source, source_path=f"/Alpha/Voice/{number}.m4a",
                    filename=f"{number}.m4a", media_type="audio/mp4",
                    size_bytes=3, sha256="synthetic", data=b"abc", duration_seconds=1,
                    received_at=at, dropbox_modified_at=at, webhook_archived=True,
                ))
                session.add(WatchEarlyCard(
                    **source, body=marker(note_id, 0), title="synthetic",
                    detected_at=at, source_modified_at=at, state="saved",
                    issue_number=number, issue_url=f"https://github.com/{REPOSITORY}/issues/{number}",
                ))

        calls: list[str] = []
        posts: dict[int, list[dict]] = {7: [], 8: []}
        long_text = "первая строка\n" + "текст " * 3000 + "\nпоследняя строка"

        async def synthetic_speech(settings, factory, http, note):
            assert note.transcription_state == "processing"
            calls.append(note.id)
            async with factory.begin() as session:
                await session.execute(
                    update(WatchVoiceNote).where(WatchVoiceNote.id == note.id).values(
                        transcription_state="transcribed", transcript=long_text,
                        transcribed_at=now, model="gpt-4o-mini-transcribe",
                    )
                )

        async def token(*args):
            return "synthetic"

        def github(request):
            path = request.url.path
            repo = f"/repos/{REPOSITORY}"
            assert str(request.url).startswith(API)
            if path == repo:
                assert request.method == "GET"
                return httpx.Response(200, json=dict(
                    full_name=REPOSITORY, id=1336157811, private=True, archived=False,
                    has_issues=True, owner={"id": 23397294},
                ))
            number = int(path.split("/")[5])
            base = f"https://github.com/{REPOSITORY}/issues/{number}"
            actor = {"login": "watch-app[bot]"}
            created = now.isoformat()
            if path == f"{repo}/issues/{number}":
                assert request.method == "GET"
                body = second_card_body if number == 8 else marker(notes[number], 0)
                return httpx.Response(200, json=dict(
                    number=number, html_url=base, created_at=created, user=actor, body=body
                ))
            assert path == f"{repo}/issues/{number}/comments"
            if request.method == "GET":
                return httpx.Response(200, json=posts[number])
            assert request.method == "POST"
            comment = dict(
                html_url=f"{base}#issuecomment-{1 + len(posts[number])}", created_at=created,
                user=actor, body=json.loads(request.content)["body"],
            )
            posts[number].append(comment)
            if number == 7 and len(posts[7]) == 1:
                raise httpx.ReadTimeout("synthetic lost acknowledgement")
            return httpx.Response(201, json=comment)

        monkeypatch.setattr(watch_voice, "transcribe_claimed_note", synthetic_speech)
        monkeypatch.setattr(worker, "issue_mark_token", token)
        monkeypatch.setattr(worker, "transcription_workload_identity", lambda _: object())
        installation = WatchAppInstallation(1, 2, "watch-app")

        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(github)) as http:
                async def run(mode):
                    await worker.drain_watch_transcription(
                        cfg, factory, http, "synthetic", installation, (mode, CANARY)
                    )

                await run("future")
                assert calls == []
                await run("canary")
                assert calls == [CANARY] and len(posts[7]) == 1
                async with factory() as session:
                    note = await session.get(WatchVoiceNote, CANARY)
                    assert note.delivery_state == "unknown"
                await run("future")
                assert calls == [CANARY]
                await run("canary")
                await run("canary")
                assert calls == [CANARY]
                assert len(posts[7]) == len(transcript_parts(long_text)) == 2
                assert "".join(c["body"].split("\n\n", 2)[2] for c in posts[7]) == long_text
                assert posts[7][0]["body"].startswith(marker(CANARY, 1))
                await run("future")
                assert calls == [CANARY]
                second_card_body = marker(FUTURE, 0)
                await run("future")
                await run("future")
                assert calls == [CANARY, FUTURE]
                assert len(posts[8]) == 2
                async with factory() as session:
                    note = await session.get(WatchVoiceNote, HISTORY)
                    assert note.transcription_state == "received"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
