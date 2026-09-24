import asyncio
import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.api.mcp_routes import (
    _effective_phone_call_media_type,
    _parse_latest_call_selector,
)
from app.application.telegram_voice_transcription import (
    DownloadedVoice,
    TelegramVoiceError,
    TelegramVoiceReference,
    TranscribeTelegramVoiceCommand,
)
from app.core.config import Settings
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer
from app.worker.dropbox_calls import (
    _CALL_TRANSCRIPTION_BUDGET_SCOPE_KIND,
    _CALL_TRANSCRIPTION_BUDGET_SCOPE_REF,
    DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES,
    DropboxCallWorkerError,
    _audio_media_type,
    _is_oversize_audio_file,
    _is_supported_file,
    _mark_superseded_revisions,
    _probe_duration_seconds,
    _recover_duration_unknown_recordings,
    _resume_received_recordings,
    _validate_settings,
)


def test_dropbox_call_intake_is_default_off() -> None:
    settings = Settings()

    assert settings.gate_dropbox_call_intake_enabled is False
    assert settings.dropbox_call_inbox_path == "/Alpha/Calls"


def test_dropbox_call_intake_rejects_another_runtime_path() -> None:
    settings = Settings(
        DROPBOX_CALL_INBOX_PATH="/Alpha/Camera",
        DROPBOX_CALL_APP_KEY="app-key",
        DROPBOX_CALL_APP_SECRET="app-secret",
        DROPBOX_CALL_REFRESH_TOKEN="refresh-token",
        DROPBOX_CALL_ACCOUNT_ID="account-id",
        OPENAI_API_KEY="openai-key",
    )

    with pytest.raises(
        DropboxCallWorkerError,
        match="dropbox_call_inbox_path_invalid",
    ):
        _validate_settings(settings)


@pytest.mark.parametrize(
    ("selector", "payload_kind"),
    [
        ("call:latest", "audio"),
        ("call:latest-file", "file"),
        ("call:latest-transcript", "transcript"),
    ],
)
def test_latest_call_selectors_are_exact(
    selector: str,
    payload_kind: str,
) -> None:
    assert _parse_latest_call_selector(selector) == payload_kind
    assert _parse_latest_call_selector("voice:latest:ru") is None


@pytest.mark.parametrize(
    "selector",
    [
        "call:latest:",
        "call:latest-transcript:anything",
        "call:latest-file:anything",
    ],
)
def test_latest_call_selectors_fail_closed(selector: str) -> None:
    with pytest.raises(ValueError):
        _parse_latest_call_selector(selector)


def test_dropbox_filter_accepts_bounded_audio_only() -> None:
    entry = {
        ".tag": "file",
        "name": "Call 2026-07-25.m4a",
        "path_display": "/Alpha/Calls/Call 2026-07-25.m4a",
        "size": 42_000,
    }

    assert _is_supported_file(entry) is True
    assert (
        _is_supported_file(
            {
                **entry,
                "path_display": "/Alpha/Camera/Call 2026-07-25.m4a",
            }
        )
        is False
    )
    assert (
        _is_supported_file(
            {
                **entry,
                "path_display": "/Alpha/Calls-archive/Call 2026-07-25.m4a",
            }
        )
        is False
    )
    assert _is_supported_file({**entry, "name": "photo.jpg"}) is False
    assert _is_supported_file({**entry, "size": 37_585_783}) is True
    oversize = {**entry, "size": DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES + 1}
    assert _is_supported_file(oversize) is False
    # The separate intake guard reports it instead of advancing the cursor.
    assert _is_oversize_audio_file(oversize) is True
    assert _is_oversize_audio_file({**oversize, "name": "photo.jpg"}) is False
    assert _is_supported_file({**entry, ".tag": "deleted"}) is False


def test_dropbox_call_uses_existing_hard_limited_voice_budget() -> None:
    command = TranscribeTelegramVoiceCommand(
        voice=TelegramVoiceReference(
            file_id="id:file",
            file_unique_id="rev:1",
            declared_size_bytes=42,
            duration_seconds=3,
        ),
        workspace_key="mark-personal",
        connector_ref=_CALL_TRANSCRIPTION_BUDGET_SCOPE_REF,
        reservation_key="dropbox-call-v1:test",
        period_key="2026-07",
        requested_microusd=300,
        occurred_at=datetime.now(UTC),
        enabled=True,
        budget_scope_kind=_CALL_TRANSCRIPTION_BUDGET_SCOPE_KIND,
    )

    assert command.budget_scope_kind == "telegram_connector"
    assert command.connector_ref == "telegram_business"


def test_transcription_budget_scope_rejects_unbounded_value() -> None:
    with pytest.raises(TelegramVoiceError):
        TranscribeTelegramVoiceCommand(
            voice=TelegramVoiceReference(
                file_id="id:file",
                file_unique_id="rev:1",
                declared_size_bytes=42,
                duration_seconds=3,
            ),
            workspace_key="mark-personal",
            connector_ref="dropbox:account",
            reservation_key="dropbox-call-v1:test",
            period_key="2026-07",
            requested_microusd=300,
            occurred_at=datetime.now(UTC),
            enabled=True,
            budget_scope_kind="x" * 65,
        )


def test_m4a_media_type_is_deterministic_without_host_mime_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.worker.dropbox_calls.mimetypes.guess_type",
        lambda _filename: (None, None),
    )
    monkeypatch.setattr(
        "app.api.mcp_routes.mimetypes.guess_type",
        lambda _filename: (None, None),
    )

    assert _audio_media_type("Call.m4a") == "audio/mp4"
    assert (
        _effective_phone_call_media_type(
            "application/octet-stream",
            "Call.m4a",
        )
        == "audio/mp4"
    )


def test_received_call_is_resumed_after_worker_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_id = uuid.uuid4()
    recording = SimpleNamespace(
        id=recording_id,
        transcription_status="received",
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [recording_id])

        async def get(self, _model, requested_id):
            assert requested_id == recording_id
            return recording

    def session_factory():
        return FakeSession()

    transcribe = AsyncMock()
    monkeypatch.setattr(
        "app.worker.dropbox_calls._transcribe_recording",
        transcribe,
    )

    settings = Settings(
        GATE_WORKSPACE_ID="mark-personal",
        DROPBOX_CALL_ACCOUNT_ID="account-id",
    )
    http = object()
    asyncio.run(
        _resume_received_recordings(
            settings=settings,
            session_factory=session_factory,
            http=http,
        )
    )

    transcribe.assert_awaited_once_with(
        settings=settings,
        session_factory=session_factory,
        http=http,
        recording=recording,
    )


def test_seekable_probe_reads_m4a_with_moov_at_end(tmp_path) -> None:
    """A normal M4A may have its index at the end of the file."""
    import subprocess

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("real audio regression requires ffmpeg and ffprobe")
    audio = tmp_path / "tail-index.m4a"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "anullsrc=r=16000:cl=mono", "-t", "2", "-c:a", "aac",
        str(audio),
    ], check=True, timeout=15)
    assert asyncio.run(_probe_duration_seconds(audio.read_bytes())) == 2
    assert asyncio.run(_probe_duration_seconds(b"invalid m4a")) is None


def test_duration_probe_is_bounded_before_process_start() -> None:
    assert asyncio.run(_probe_duration_seconds(
        b"x" * (DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES + 1)
    )) is None


@pytest.mark.parametrize(
    ("minutes", "expected_parts"),
    [(20, 7), (30, 10), (38, 13), (60, 20)],
)
def test_long_m4a_keeps_complete_ordered_parts(minutes, expected_parts) -> None:
    seen = []

    async def chunked_runner(data):
        seen.append(data)
        return 0, tuple(f"part-{n:03d}".encode() for n in range(1, expected_parts + 1)), b""

    source = b"synthetic M4A frame"
    normalized = asyncio.run(TelegramVoiceNormalizer(
        chunked_runner=chunked_runner,
    ).normalize(DownloadedVoice(
        data=source, filename="call.m4a", media_type="application/octet-stream",
        duration_seconds=minutes * 60,
    )))
    parts = (normalized, *normalized.additional_parts)
    assert seen == [source]
    assert len(parts) == expected_parts
    assert [part.filename for part in parts] == [
        f"telegram-voice-part-{n:03d}.mp3" for n in range(1, expected_parts + 1)
    ]
    assert [part.data for part in parts] == [
        f"part-{n:03d}".encode() for n in range(1, expected_parts + 1)
    ]


@pytest.mark.parametrize(
    (
        "duration", "prior_reservation", "source_reason", "valid_archive",
        "expected_status", "expected_reason",
    ),
    [
        (1777, False, "dropbox_call_duration_unknown", True,
         "received", "dropbox_call_duration_recovered"),
        (3600, False, "dropbox_call_duration_probe_failed", True,
         "received", "dropbox_call_duration_recovered"),
        (3601, False, "dropbox_call_duration_probe_failed", True,
         "rejected", "dropbox_call_duration_exceeded"),
        (None, False, "dropbox_call_duration_probe_failed", True,
         "rejected", "dropbox_call_media_unreadable"),
        (1777, False, "dropbox_call_duration_probe_failed", False,
         "rejected", "dropbox_call_archive_integrity_failed"),
        (1777, True, "dropbox_call_duration_unknown", True,
         "rejected", "dropbox_call_recovery_prior_reservation"),
    ],
)
def test_duration_recovery_never_replays_prior_provider_work(
    monkeypatch, duration, prior_reservation, source_reason, valid_archive,
    expected_status, expected_reason,
) -> None:
    data = b"synthetic audio"
    row = SimpleNamespace(
        id=uuid.uuid4(), workspace_key="mark-personal", source_account_id="account-id",
        source_file_id="id:exact", source_revision="revision-exact",
        transcription_status="rejected", transcription_reason_code=source_reason,
        duration_seconds=None, transcript_text=None, size_bytes=len(data),
        sha256=hashlib.sha256(data if valid_archive else b"other bytes").hexdigest(),
        data=data, transcribed_at=datetime.now(UTC),
    )
    class FakeSession:
        def __init__(self):
            self.scalar_count = 0
            self.commits = 0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            pass
        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [row])
        async def scalar(self, _statement):
            self.scalar_count += 1
            return row if self.scalar_count == 1 else (uuid.uuid4() if prior_reservation else None)
        async def commit(self):
            self.commits += 1
    sessions = []
    def factory():
        session = FakeSession()
        sessions.append(session)
        return session
    probe = AsyncMock(return_value=duration)
    monkeypatch.setattr("app.worker.dropbox_calls._probe_duration_seconds", probe)
    asyncio.run(_recover_duration_unknown_recordings(
        settings=Settings(GATE_WORKSPACE_ID="mark-personal", DROPBOX_CALL_ACCOUNT_ID="account-id"),
        session_factory=factory,
    ))
    assert row.transcription_status == expected_status
    assert row.transcription_reason_code == expected_reason
    assert sessions[-1].commits == 1
    assert row.duration_seconds == (
        duration if duration and not prior_reservation and valid_archive else None
    )
    assert probe.await_count == (1 if valid_archive else 0)


def test_probe_uses_decoded_audio_when_container_duration_is_missing(monkeypatch) -> None:
    class FakeProcess:
        def __init__(self, returncode, output):
            self.returncode = returncode
            self.output = output

        async def communicate(self):
            return self.output, b""

    calls = []

    async def create_process(*args, **_kwargs):
        calls.append(args[0])
        if args[0] == "ffprobe":
            return FakeProcess(1, b"")
        return FakeProcess(0, b"out_time_us=1776377583\nprogress=end\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    assert asyncio.run(_probe_duration_seconds(b"some audio bytes")) == 1777
    assert calls == ["ffprobe", "ffmpeg"]


def test_probe_rejects_failed_decode_even_with_progress(monkeypatch) -> None:
    class FakeProcess:
        def __init__(self, returncode, output):
            self.returncode = returncode
            self.output = output

        async def communicate(self):
            return self.output, b""

    async def create_process(*args, **_kwargs):
        if args[0] == "ffprobe":
            return FakeProcess(1, b"")
        return FakeProcess(1, b"out_time_us=1776377583\nprogress=end\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    assert asyncio.run(_probe_duration_seconds(b"corrupted audio")) is None


@pytest.mark.parametrize("later_transcript_exists", [False, True])
def test_unreadable_partial_revision_is_closed_only_after_final_transcript(
    later_transcript_exists,
) -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(), workspace_key="mark-personal",
        project_id=uuid.uuid4(), source_account_id="account-id",
        source_file_id="id:stable", source_revision="partial-revision",
        source_path="/Alpha/Calls/call.m4a", filename="call.m4a",
        size_bytes=3_086_377, received_at=datetime.now(UTC),
        transcription_status="rejected",
        transcription_reason_code="dropbox_call_media_unreadable",
        transcript_text=None,
    )

    class FakeSession:
        def __init__(self):
            self.lookups = 0
            self.commits = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [row.id])

        async def scalar(self, statement):
            self.lookups += 1
            if self.lookups == 1:
                return row
            query = str(statement)
            for predicate in (
                "source_file_id", "source_revision", "project_id",
                "source_path", "filename", "size_bytes", "received_at",
                "transcript_text",
            ):
                assert predicate in query
            return uuid.uuid4() if later_transcript_exists else None

        async def commit(self):
            self.commits += 1

    sessions = []

    def factory():
        session = FakeSession()
        sessions.append(session)
        return session

    asyncio.run(_mark_superseded_revisions(
        settings=Settings(GATE_WORKSPACE_ID="mark-personal", DROPBOX_CALL_ACCOUNT_ID="account-id"),
        session_factory=factory,
    ))
    assert row.transcription_status == (
        "superseded" if later_transcript_exists else "rejected"
    )
    assert row.transcription_reason_code == (
        "dropbox_call_revision_superseded" if later_transcript_exists
        else "dropbox_call_media_unreadable"
    )
    assert sessions[-1].commits == int(later_transcript_exists)
