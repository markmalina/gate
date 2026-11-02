"""Run only in the exact fallback checkout, with disposable PostgreSQL and old HTTP stubs."""

import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

ARTIFACT = "7fd322c958cccf6828a3e8fcb00a075b2d4f1f46"
BASE = "afe0c6b8519381a0007c0dcc1c145207b5d54a3f"
HEAD = "0119_watch_voice_comment_ids"
MIGRATIONS = {
    "alembic/versions/0116_watch_voice_observability.py": (
        "a7d214f93c260b2bbfba434feb18beb4f644718f"
    ),
    "alembic/versions/0117_watch_voice_early_inbox.py": "5a70c301b669b8715c9edaa72b87e7ae1b4a5312",
    "alembic/versions/0118_watch_voice_work_results.py": "2a4cf2406860e264093b44e61692abc348b79f40",
    "alembic/versions/0119_watch_voice_comment_ids.py": "6c78523a6243f1d2c7c26e57a80ce8f5910171cb",
}
NOTE_IDS = {
    index: str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            repr(("mark-personal", "synthetic-owner", file_id, revision)),
        )
    )
    for index, file_id, revision in (
        (1, "id:synthetic", "abcdef1234"),
        (2, "id:synthetic-unknown", "abcdef5678"),
    )
}


@pytest.mark.skipif(
    os.getenv("WATCH_BASELINE_FALLBACK_FIXTURE") != "1",
    reason="dedicated disposable fallback-compatibility job only",
)
def test_exact_old_actions_app_on_populated_watch0119(monkeypatch):
    import httpx
    from app.api.health_routes import router
    from app.infrastructure.readiness import build_readiness_service
    from app.infrastructure.watch_voice_actions import drain
    from app.models.watch_voice import WatchVoiceCursor, WatchVoiceDrainRun, WatchVoiceNote
    from fastapi import FastAPI
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from test_watch_voice_actions import ActionsWorld, action_settings, claims

    root = Path(__file__).resolve().parents[1]
    assert (
        Path(inspect.getfile(drain)).resolve() == root / "app/infrastructure/watch_voice_actions.py"
    )
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        == ARTIFACT
    )
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", BASE, ARTIFACT], cwd=root, text=True
    )
    assert set(changed.splitlines()) == set(MIGRATIONS)
    for path, expected in MIGRATIONS.items():
        raw = (root / path).read_bytes()
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected
    subprocess.run(
        ["git", "diff", "--exit-code", "--", "app", "alembic", "railway.toml"], cwd=root, check=True
    )

    url = os.environ["DATABASE_URL"]
    assert url in {
        "postgresql+asyncpg://postgres:postgres@localhost:5432/watch_baseline",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/watch_baseline_restore",
    }, "only the dedicated local synthetic databases are allowed"
    restored = os.getenv("WATCH_BASELINE_RESTORED_ONLY") == "1"
    world = ActionsWorld()
    world.patches(monkeypatch)
    settings = action_settings()
    assert settings.gate_watch_voice_enabled is False
    assert settings.gate_watch_voice_actions_enabled is True
    assert all(
        os.environ[name] == "false"
        for name in (
            "GATE_WATCH_VOICE_ENABLED",
            "GATE_WATCH_VOICE_EARLY_INBOX_ENABLED",
            "GATE_WATCH_VOICE_WORK_RESERVE_ENABLED",
            "GATE_WATCH_VOICE_WORK_MCP_ENABLED",
        )
    )
    now = datetime(2026, 10, 5, 15, tzinfo=UTC)
    watermark = datetime(2026, 10, 1, tzinfo=UTC)

    def upgrade(revision):
        subprocess.run(["alembic", "upgrade", revision], cwd=root, check=True, timeout=120)

    async def run():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        web = FastAPI()
        web.include_router(router)
        web.state.readiness_service = build_readiness_service(engine)

        async def ready(expected):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=web), base_url="http://fixture.invalid"
            ) as client:
                response = await client.get("/ready")
                assert response.status_code == expected
                return response.json()

        async def snapshot():
            async with factory() as session:
                cursor = await session.get(WatchVoiceCursor, ("mark-personal", "synthetic-owner"))
                notes = list(
                    (
                        await session.scalars(select(WatchVoiceNote).order_by(WatchVoiceNote.id))
                    ).all()
                )
                old_run = await session.get(WatchVoiceDrainRun, "1" * 64)
                unknown = next(n for n in notes if n.id == NOTE_IDS[2])
                return (
                    cursor.cursor,
                    cursor.future_cutover_at,
                    old_run.jwt_key,
                    unknown.data,
                    unknown.sha256,
                    unknown.transcription_state,
                    unknown.delivery_state,
                    unknown.last_error,
                    unknown.attempted_at,
                )

        try:
            if not restored:
                upgrade("0115_watch_voice_post_payment")
                assert (await ready(503))["reason"] == "migration_heads_mismatch"
                async with factory() as session:
                    session.add(
                        WatchVoiceCursor(
                            workspace_key="mark-personal",
                            account_id="synthetic-owner",
                            cursor="synthetic-cursor-before",
                            future_cutover_at=watermark,
                        )
                    )
                    session.add(
                        WatchVoiceDrainRun(run_key="1" * 64, jwt_key="2" * 64, claimed_at=now)
                    )
                    for index, state in ((1, "transcribed"), (2, "unknown")):
                        session.add(
                            WatchVoiceNote(
                                id=NOTE_IDS[index],
                                workspace_key="mark-personal",
                                account_id="synthetic-owner",
                                file_id="id:synthetic" if index == 1 else "id:synthetic-unknown",
                                revision="abcdef1234" if index == 1 else "abcdef5678",
                                source_path="/Alpha/Voice/Synthetic.m4a",
                                filename="Synthetic.m4a",
                                media_type="audio/mp4",
                                size_bytes=len(world.audio),
                                sha256=hashlib.sha256(world.audio).hexdigest(),
                                data=world.audio,
                                dropbox_modified_at=now,
                                received_at=now,
                                transcription_state=state,
                                transcript=world.transcript if index == 1 else None,
                                model="synthetic",
                                transcribed_at=now if index == 1 else None,
                                delivery_state="pending" if index == 1 else "unknown",
                                next_part=0,
                                attempted_at=now if index == 2 else None,
                                last_error="synthetic_unknown" if index == 2 else None,
                            )
                        )
                    await session.commit()
                before = await snapshot()
                upgrade(HEAD)
                assert await snapshot() == before
                assert (await ready(200))["status"] == "ready"
                async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                    kwargs = dict(
                        settings=settings,
                        factory=factory,
                        http=http,
                        claims=claims(),
                        job_token="synthetic-job-token",
                    )
                    receipt = await drain(**kwargs)
                    assert receipt["delivered"] == 1 and receipt["private_content_exposed"] is False
                    assert (await drain(**kwargs))["result"] == "replayed"
                    assert world.posts == 1 and world.provider_calls == 0
                    # A fresh admitted run must not retry an existing UNKNOWN original.
                    world.current_run = "322"
                    world.entry.update(id="id:synthetic-unknown", rev="abcdef5678")
                    world.binding.update(file_id="id:synthetic-unknown", revision="abcdef5678")
                    await drain(
                        settings=settings,
                        factory=factory,
                        http=http,
                        claims=claims(run_id="322", jti="synthetic-second-run"),
                        job_token="synthetic-job-token",
                    )
                    assert world.posts == 1 and world.provider_calls == 0
                after = await snapshot()
                assert after[1:] == before[1:]
            else:
                after = await snapshot()
                assert after[1] == watermark and after[2] == "2" * 64
                assert after[3:] == (
                    world.audio,
                    hashlib.sha256(world.audio).hexdigest(),
                    "unknown",
                    "unknown",
                    "synthetic_unknown",
                    now,
                )
                # Restored journals must still suppress effects, including a
                # fresh admitted run encountering the original UNKNOWN note.
                async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                    replay = await drain(
                        settings=settings,
                        factory=factory,
                        http=http,
                        claims=claims(),
                        job_token="synthetic-job-token",
                    )
                    assert replay["result"] == "replayed"
                    world.current_run = "323"
                    world.entry.update(id="id:synthetic-unknown", rev="abcdef5678")
                    world.binding.update(file_id="id:synthetic-unknown", revision="abcdef5678")
                    await drain(
                        settings=settings,
                        factory=factory,
                        http=http,
                        claims=claims(run_id="323", jti="synthetic-restored-run"),
                        job_token="synthetic-job-token",
                    )
                    assert world.posts == 0 and world.provider_calls == 0
                assert await snapshot() == after

            assert after[0] == "synthetic-cursor"
            assert (await ready(200))["status"] == "ready"
            async with factory() as session:
                heads = (
                    (await session.execute(text("SELECT version_num FROM alembic_version")))
                    .scalars()
                    .all()
                )
                assert heads == [HEAD]
                notes = list(
                    (
                        await session.scalars(select(WatchVoiceNote).order_by(WatchVoiceNote.id))
                    ).all()
                )
                assert len(notes) == 2
                assert next(n for n in notes if n.id == NOTE_IDS[1]).delivery_state == "delivered"
                assert all(
                    n.data == world.audio and n.sha256 == hashlib.sha256(world.audio).hexdigest()
                    for n in notes
                )
                assert (
                    await session.execute(text("SELECT count(*) FROM watch_voice_drain_runs"))
                ).scalar_one() == (4 if restored else 3)
                assert (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM watch_voice_notes "
                            "WHERE transcript_origin = 'gate'"
                        )
                    )
                ).scalar_one() == 2
                version = (await session.execute(text("SHOW server_version"))).scalar_one()
            evidence = {
                "schema": "gate.watch.baseline-fallback-fixture/v1",
                "result": "ACCEPT",
                "scope": "DISPOSABLE_SYNTHETIC_ONLY",
                "artifact_sha": ARTIFACT,
                "baseline_sha": BASE,
                "migration_head": HEAD,
                "postgres_version": version,
                "old_actions_app": True,
                "real_database_ready_http": True,
                "restore_checked": restored,
                "real_drain_with_http_stubs": True,
                "restored_replay_and_unknown_no_effects": restored,
                "external_provider_calls": 0,
                "originals_cursor_unknown_and_replay_preserved": True,
                "production_authorized": False,
            }
            name = (
                "watch-baseline-fallback-restore.json"
                if restored
                else "watch-baseline-fallback.json"
            )
            Path(name).write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
        finally:
            await engine.dispose()

    asyncio.run(run())
