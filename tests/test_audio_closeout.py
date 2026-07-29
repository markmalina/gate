# ruff: noqa: I001
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.communication_feed_mcp import _call_feed_item, _voice_feed_item
from app.api.mcp_routes import _voice_account_selector_scope
from app.application.audio_summary import AUDIO_SUMMARY_MODEL, build_audio_summary
from app.application.telegram_voice_transcription import (
    OPENAI_TRANSCRIPTION_LIMIT_BYTES,
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
)
from app.models.domain import DropboxCallInboxState
from app.worker.dropbox_calls import _is_supported_file


def test_audio_archive_bound_can_exceed_provider_request_bound() -> None:
    assert TELEGRAM_DOWNLOAD_LIMIT_BYTES == 25 * 1024 * 1024
    assert OPENAI_TRANSCRIPTION_LIMIT_BYTES == 25_000_000
    assert TELEGRAM_DOWNLOAD_LIMIT_BYTES > OPENAI_TRANSCRIPTION_LIMIT_BYTES


def test_dropbox_accepts_previously_skipped_irina_call_size() -> None:
    entry = {
        ".tag": "file",
        "name": "Вызов Ирина Яворская.m4a",
        "path_display": "/Alpha/Calls/Вызов Ирина Яворская.m4a",
        "size": 25_505_021,
    }
    assert _is_supported_file(entry) is True
    entry["size"] = TELEGRAM_DOWNLOAD_LIMIT_BYTES + 1
    assert _is_supported_file(entry) is False


def test_new_dropbox_state_requires_one_bounded_backfill() -> None:
    state = DropboxCallInboxState(account_id="dbid:test", backfill_version=0)
    assert state.backfill_version == 0


def test_audio_summary_is_normalized_and_bounded() -> None:
    transcript = "  Первая договорённость.   Вторая договорённость.  " + (
        "Подробности разговора. " * 60
    ) + "Финальный следующий шаг."
    summary = build_audio_summary(transcript)
    assert summary.startswith("Первая договорённость. Вторая договорённость.")
    assert summary.endswith("Финальный следующий шаг.")
    assert len(summary) <= 600
    assert AUDIO_SUMMARY_MODEL == "extractive-ru-v1"


def test_audio_summary_rejects_empty_transcript() -> None:
    with pytest.raises(ValueError, match="audio_summary_transcript_empty"):
        build_audio_summary("   ")


def test_call_feed_exposes_persisted_summary() -> None:
    now = datetime.now(UTC)
    item = _call_feed_item(
        SimpleNamespace(
            id="call-id",
            received_at=now,
            transcript_text="Полный текст звонка.",
            transcription_status="transcribed",
            summary_text="Краткий итог звонка.",
            summary_status="summarized",
        ),
        UTC,
    )
    assert item["summary"] == "Краткий итог звонка."
    assert item["summary_status"] == "summarized"
    assert item["semantic_input_private"] == "Полный текст звонка."


def test_voice_feed_exposes_persisted_summary() -> None:
    now = datetime.now(UTC)
    item = _voice_feed_item(
        SimpleNamespace(
            id="voice-id",
            direction="outbound",
            received_at=now,
            transcript_text="Полный текст голосового.",
            transcription_status="transcribed",
            summary_text="Краткий итог голосового.",
            summary_status="summarized",
        ),
        SimpleNamespace(alias="telegram_ru", country_code="ru"),
        None,
        UTC,
    )
    assert item["summary"] == "Краткий итог голосового."
    assert item["summary_status"] == "summarized"


def test_voice_latest_selector_resolves_documented_country_suffixes() -> None:
    assert _voice_account_selector_scope("ru") == ("ru", "ru")
    assert _voice_account_selector_scope("by") == ("by", "by")
    assert _voice_account_selector_scope("telegram_mark_ru") == (
        "telegram_mark_ru",
        None,
    )
