"""Exercise the signed webhook and both queues without remote services."""

import asyncio
import hmac
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.api.deps import get_db_session
from app.api.dropbox_call_routes import router
from app.application.telegram_voice_transcription import TelegramVoiceOutcome
from app.core.config import Settings, get_settings
from app.models.billing import BillingBudgetReservationModel
from app.models.canonical import Workspace
from app.models.domain import (
    UNCLASSIFIED_PROJECT_ID,
    DropboxCallInboxState,
    DropboxCallIntakeRejection,
    PhoneCallRecording,
    Project,
)
from app.worker import dropbox_calls
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _source(name: str, size: int) -> dict[str, str | int]:
    return {
        ".tag": "file", "id": f"id:{name}", "rev": f"rev-{name}",
        "name": f"{name}.m4a", "path_display": f"/Alpha/Calls/{name}.m4a", "size": size,
    }


@pytest.mark.anyio
async def test_call_webhook_advances_past_large_file_while_transcription_is_busy(monkeypatch):
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL required to exercise the real PostgreSQL queues")
    schema = f"calls_queue_{uuid4().hex}"
    admin = create_async_engine(database_url)
    engine = create_async_engine(database_url, connect_args={
        "server_settings": {"search_path": schema},
    })
    settings = Settings(
        GATE_WORKSPACE_ID="mark-personal", GATE_DROPBOX_CALL_INTAKE_ENABLED=True,
        GATE_DROPBOX_CALL_POLL_SECONDS=300,
        DROPBOX_CALL_ACCOUNT_ID="test-owner", DROPBOX_CALL_APP_KEY="synthetic",
        DROPBOX_CALL_APP_SECRET="synthetic", DROPBOX_CALL_REFRESH_TOKEN="synthetic",
        OPENAI_API_KEY="synthetic", GATE_WATCH_WEBHOOK_ENABLED=False,
    )
    workspace_id = uuid4()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_processing = asyncio.Event()
    second_delta_started = asyncio.Event()
    finish_second_delta = asyncio.Event()
    replay_scanned = asyncio.Event()
    received_text = asyncio.Event()
    downloads = []
    dispatches = []

    async def list_changes(*, cursor, **_):
        entries = []
        match cursor:
            case None:
                entries = [_source("slow", 4)]
                cursor = "first"
            case "first":
                second_delta_started.set()
                await finish_second_delta.wait()
                entries = [
                    _source("oversize", dropbox_calls.DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES + 1),
                    _source("next", 4),
                ]
                cursor = "second"
            case "second":
                async with sessions() as session:
                    state = await session.get(DropboxCallInboxState, "test-owner")
                    assert state.pending
                entries = [_source("next", 4)]
                cursor = "replayed"
        return {"entries": entries, "cursor": cursor, "has_more": False}

    commit_cursor = dropbox_calls._commit_cursor

    async def observe_committed_cursor(**kwargs):
        await commit_cursor(**kwargs)
        if kwargs["cursor"] == "replayed":
            replay_scanned.set()

    async def download(*, revision, **_):
        downloads.append(revision)
        return b"wave", "audio/mp4"

    async def duration(_data):
        return 1

    async def transcribe(_service, command):
        dispatches.append(command.voice.file_id)
        await command.on_recognition_start(datetime.now(UTC))
        if command.voice.file_id == "id:slow":
            async with sessions.begin() as session:
                session.add(BillingBudgetReservationModel(
                    workspace_id=workspace_id, reservation_key=command.reservation_key,
                    product="gate", scope_kind="telegram_connector", scope_ref="telegram_business",
                    operation="transcription", period_key=command.period_key,
                    reserved_microusd=5000, state="reserved",
                ))
            first_processing.set()
            await asyncio.Event().wait()
        return SimpleNamespace(
            outcome=TelegramVoiceOutcome.TRANSCRIBED, reason_code="telegram_voice_transcribed",
            alpha_input_text="synthetic transcript",
        )

    async def account_token(*_):
        return "synthetic"

    async def no_maintenance(*_, **__):
        pass

    async def after_transcription(**_):
        if "id:next" in dispatches:
            received_text.set()

    monkeypatch.setattr(dropbox_calls, "_refresh_access_token", account_token)
    monkeypatch.setattr(dropbox_calls, "_verify_account_id", no_maintenance)
    monkeypatch.setattr(dropbox_calls, "_list_changes", list_changes)
    monkeypatch.setattr(dropbox_calls, "_commit_cursor", observe_committed_cursor)
    monkeypatch.setattr(dropbox_calls, "_download_revision", download)
    monkeypatch.setattr(dropbox_calls, "_probe_duration_seconds", duration)
    monkeypatch.setattr(dropbox_calls.TelegramVoiceTranscriptionService, "transcribe", transcribe)
    monkeypatch.setattr(dropbox_calls, "_recover_authorized_telegram_voices", no_maintenance)
    monkeypatch.setattr(dropbox_calls, "_recover_duration_unknown_recordings", no_maintenance)
    monkeypatch.setattr(dropbox_calls, "_summarize_ready_audio", after_transcription)
    monkeypatch.setattr(dropbox_calls, "_mark_superseded_revisions", no_maintenance)
    monkeypatch.setattr(dropbox_calls, "reconcile_exact_call_copies", no_maintenance)

    app = FastAPI()
    app.include_router(router)
    app.state.dropbox_call_wake_event = asyncio.Event()

    async def get_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_session
    app.dependency_overrides[get_settings] = lambda: settings

    async def notify(http, *, valid=True):
        body = json.dumps({"list_folder": {"accounts": ["test-owner"]}}).encode()
        signature = hmac.digest(b"synthetic", body, "sha256").hex()
        response = await http.post("/dropbox/calls/webhook", content=body, headers={
            "X-Dropbox-Signature": signature if valid else "bad",
        })
        assert response.status_code == (200 if valid else 401)

    def start():
        return asyncio.create_task(dropbox_calls.run_dropbox_call_worker_loop(
            settings=settings, session_factory=sessions,
            intake_wake=app.state.dropbox_call_wake_event,
        ))

    async def stop(task):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    task = None
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            for model in (
                Project, DropboxCallInboxState, DropboxCallIntakeRejection, PhoneCallRecording,
                Workspace, BillingBudgetReservationModel,
            ):
                await connection.run_sync(model.__table__.create)
        async with sessions.begin() as session:
            session.add(Workspace(id=workspace_id, key="mark-personal", display_name="Test"))
            session.add(Project(
                id=UNCLASSIFIED_PROJECT_ID, alias="intake", name="Intake", adapter_type="test",
            ))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as http:
            task = start()
            await asyncio.wait_for(first_processing.wait(), timeout=5)
            await notify(http, valid=False)
            assert not app.state.dropbox_call_wake_event.is_set()
            await notify(http)
            await asyncio.wait_for(second_delta_started.wait(), timeout=5)
            await notify(http)
            finish_second_delta.set()
            await asyncio.wait_for(replay_scanned.wait(), timeout=5)
            async with sessions() as session:
                records = (await session.scalars(select(PhoneCallRecording))).all()
                reasons = (await session.scalars(select(DropboxCallIntakeRejection))).all()
                state = await session.get(DropboxCallInboxState, "test-owner")
                assert {row.source_file_id: row.transcription_status for row in records} == {
                    "id:slow": "processing", "id:next": "received",
                }
                assert [row.reason_code for row in reasons] == [
                    "dropbox_call_download_limit_exceeded",
                ]
                assert state.cursor == "replayed" and not state.pending

            await stop(task)
            async with sessions.begin() as session:
                interrupted = await session.scalar(select(PhoneCallRecording).where(
                    PhoneCallRecording.source_file_id == "id:slow",
                ))
                interrupted.transcribed_at = datetime.now(UTC) - timedelta(hours=2)
            app.state.dropbox_call_wake_event = asyncio.Event()
            task = start()
            await asyncio.wait_for(received_text.wait(), timeout=5)
            async with sessions() as session:
                next_call = await session.scalar(select(PhoneCallRecording).where(
                    PhoneCallRecording.source_file_id == "id:next",
                ))
                assert next_call.transcription_status == "transcribed"
                assert next_call.transcript_text == "synthetic transcript"
                assert (next_call.received_at == next_call.transcription_queued_at
                        <= next_call.recognition_started_at <= next_call.text_saved_at)
                assert next_call.transcription_stopped_at is None
                interrupted = await session.get(PhoneCallRecording, interrupted.id)
                assert interrupted.transcription_status == "unknown"
                assert interrupted.transcription_reason_code == (
                    "dropbox_call_interrupted_after_reservation"
                )
                assert interrupted.transcription_stop_reason == (
                    "dropbox_call_recognition_interrupted"
                )
                assert interrupted.recognition_started_at <= interrupted.transcription_stopped_at
                assert interrupted.text_saved_at is None
            assert downloads == ["rev-slow", "rev-next"]
            assert dispatches == ["id:slow", "id:next"]
    finally:
        if task is not None:
            await stop(task)
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
