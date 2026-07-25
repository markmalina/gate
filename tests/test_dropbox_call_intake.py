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
    TelegramVoiceError,
    TelegramVoiceReference,
    TranscribeTelegramVoiceCommand,
)
from app.core.config import Settings
from app.worker.dropbox_calls import (
    DropboxCallWorkerError,
    _audio_media_type,
    _is_supported_file,
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
    assert _is_supported_file({**entry, "size": 20 * 1024 * 1024 + 1}) is False
    assert _is_supported_file({**entry, ".tag": "deleted"}) is False


def test_transcription_budget_scope_can_identify_dropbox() -> None:
    command = TranscribeTelegramVoiceCommand(
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
        budget_scope_kind="dropbox_account",
    )

    assert command.budget_scope_kind == "dropbox_account"


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


@pytest.mark.asyncio
async def test_received_call_is_resumed_after_worker_restart(
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
    await _resume_received_recordings(
        settings=settings,
        session_factory=session_factory,
        http=http,
    )

    transcribe.assert_awaited_once_with(
        settings=settings,
        session_factory=session_factory,
        http=http,
        recording=recording,
    )
