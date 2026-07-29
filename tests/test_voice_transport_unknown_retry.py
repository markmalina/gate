from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from app.worker.dropbox_calls import (
    DropboxCallWorkerError,
    _telegram_voice_recovery_reservation_key,
)


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0062_voice_transport_unknown_retry.py"
    )
    spec = importlib.util.spec_from_file_location(
        "voice_transport_unknown_retry",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_retry_scope_fails_closed() -> None:
    migration = _migration()

    migration._require_exact_scope(1)
    for candidate_count in (0, 2):
        with pytest.raises(
            RuntimeError,
            match="voice_transport_unknown_retry_scope_mismatch",
        ):
            migration._require_exact_scope(candidate_count)

    assert migration.down_revision == "0061_voice_reconcile"
    assert migration._TRANSPORT_UNKNOWN == (
        "openai_transcription_transport_unknown"
    )


def test_exact_retry_is_atomic_content_free_and_reversible() -> None:
    root = Path(__file__).parents[1]
    migration = (
        root
        / "alembic"
        / "versions"
        / "0062_voice_transport_unknown_retry.py"
    ).read_text(encoding="utf-8")
    worker = (root / "app" / "worker" / "dropbox_calls.py").read_text(
        encoding="utf-8"
    )

    assert "WITH eligible AS MATERIALIZED" in migration
    assert "scope.candidate_count = 1" in migration
    assert "octet_length(a.data) > 0" in migration
    assert "a.transcript_text IS NULL" in migration
    assert "a.summary_text IS NULL" in migration
    assert "transcription_recovery_authorized_at = now()" in migration
    assert "SET transcription_recovery_authorized_at = NULL" in migration
    assert ".limit(2)" in worker
    assert "telegram_voice_recovery_scope_ambiguous" in worker
    assert "archive.transcription_recovery_authorized_at = None" in worker
    assert "telegram-voice-recovery-v2:" in worker
    assert "telegram-voice-recovery-v1:" not in worker


def test_second_retry_uses_one_stable_new_reservation_generation() -> None:
    archive_id = uuid4()
    authorized_at = datetime(2026, 7, 29, 17, 15, tzinfo=UTC)

    first = _telegram_voice_recovery_reservation_key(
        archive_id,
        authorized_at,
    )
    replay = _telegram_voice_recovery_reservation_key(
        archive_id,
        authorized_at,
    )
    later = _telegram_voice_recovery_reservation_key(
        archive_id,
        authorized_at + timedelta(microseconds=1),
    )

    assert first == replay
    assert first != later
    assert first.startswith("telegram-voice-recovery-v2:")
    assert len(first) <= 128

    with pytest.raises(
        DropboxCallWorkerError,
        match="telegram_voice_recovery_authorization_time_invalid",
    ):
        _telegram_voice_recovery_reservation_key(
            archive_id,
            datetime(2026, 7, 29, 17, 15),
        )
