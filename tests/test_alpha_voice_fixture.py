"""End-to-end synthetic Alpha voice product contract without provider or delivery."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.alpha_voice import (
    AlphaVoiceDomainError,
    TelegramVoiceMetadata,
    TranscriptionJobKey,
    TranscriptionJobState,
    VoiceTranscriptionJob,
    new_queued_transcription_job,
    start_transcription_job,
)
from app.application.alpha_voice_fixture import (
    DeterministicAlphaVoiceFixtureRunner,
    RunAlphaVoiceFixtureCommand,
)

NOW = datetime(2026, 7, 16, 20, 0, tzinfo=UTC)


def _metadata() -> TelegramVoiceMetadata:
    return TelegramVoiceMetadata(
        file_id="fixture-file-sensitive",
        file_unique_id="fixture-unique-sensitive",
        duration_seconds=2,
        file_size_bytes=5,
        mime_type="audio/ogg",
    )


def _queued() -> VoiceTranscriptionJob:
    return new_queued_transcription_job(
        TranscriptionJobKey("workspace-mark", "canonical-message-sensitive"),
        now=NOW,
    )


def test_fixture_runs_transcription_understanding_and_answer_without_delivery() -> None:
    runner = DeterministicAlphaVoiceFixtureRunner(
        transcript="Расскажи, что происходит с проектом",
        understanding="вы хотите узнать текущий статус проекта",
        answer="Безопасный тестовый контур готов.",
    )
    result = asyncio.run(
        runner.run(
            RunAlphaVoiceFixtureCommand(
                queued_job=_queued(),
                metadata=_metadata(),
                audio_bytes=b"voice",
                started_at=NOW + timedelta(seconds=1),
                completed_at=NOW + timedelta(seconds=2),
            )
        )
    )

    assert result.completed_job.state is TranscriptionJobState.COMPLETED
    assert result.completed_job.transcript == "Расскажи, что происходит с проектом"
    assert result.render() == (
        "Я понял так: вы хотите узнать текущий статус проекта\n\n"
        "Ответ: Безопасный тестовый контур готов."
    )
    assert result.telegram_delivery_blocked is True
    assert result.provider_network_used is False
    assert result.operation_ref.startswith("fixture-")


def test_fixture_is_deterministic_for_the_same_audio() -> None:
    runner = DeterministicAlphaVoiceFixtureRunner(
        transcript="Одинаковая расшифровка",
        understanding="одинаковый смысл",
        answer="одинаковый ответ",
    )
    command = RunAlphaVoiceFixtureCommand(
        queued_job=_queued(),
        metadata=_metadata(),
        audio_bytes=b"voice",
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=2),
    )

    first = asyncio.run(runner.run(command))
    second = asyncio.run(runner.run(command))

    assert first.operation_ref == second.operation_ref
    assert first.render() == second.render()


def test_fixture_rejects_an_already_started_job() -> None:
    running = start_transcription_job(
        _queued(),
        now=NOW + timedelta(seconds=1),
        provider_operation_ref="fixture-existing",
    )

    with pytest.raises(AlphaVoiceDomainError, match="voice_fixture_job_not_queued"):
        RunAlphaVoiceFixtureCommand(
            queued_job=running,
            metadata=_metadata(),
            audio_bytes=b"voice",
            started_at=NOW + timedelta(seconds=2),
            completed_at=NOW + timedelta(seconds=3),
        )


def test_fixture_source_has_no_provider_telegram_or_file_io() -> None:
    source = (
        Path(__file__).parents[1]
        / "app"
        / "application"
        / "alpha_voice_fixture.py"
    ).read_text(encoding="utf-8")

    assert "import openai" not in source.casefold()
    assert "import httpx" not in source
    assert "send_message(" not in source
    assert ".download(" not in source
    assert "open(" not in source


def test_fixture_repr_hides_audio_transcript_and_answer() -> None:
    runner = DeterministicAlphaVoiceFixtureRunner(
        transcript="secret-transcript",
        understanding="secret-understanding",
        answer="secret-answer",
    )
    command = RunAlphaVoiceFixtureCommand(
        queued_job=_queued(),
        metadata=_metadata(),
        audio_bytes=b"secret-audio",
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=2),
    )
    result = asyncio.run(runner.run(command))
    combined = " ".join(map(repr, (runner, command, result)))

    for secret in (
        "secret-transcript",
        "secret-understanding",
        "secret-answer",
        "secret-audio",
        "fixture-file-sensitive",
        "fixture-unique-sensitive",
        "canonical-message-sensitive",
    ):
        assert secret not in combined
