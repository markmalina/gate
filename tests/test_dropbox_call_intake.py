from datetime import UTC, datetime

import pytest
from app.api.mcp_routes import _parse_latest_call_selector
from app.application.telegram_voice_transcription import (
    TelegramVoiceError,
    TelegramVoiceReference,
    TranscribeTelegramVoiceCommand,
)
from app.core.config import Settings
from app.worker.dropbox_calls import _is_supported_file


def test_dropbox_call_intake_is_default_off() -> None:
    settings = Settings()

    assert settings.gate_dropbox_call_intake_enabled is False
    assert settings.dropbox_call_inbox_path == "/Alpha/Calls"


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
