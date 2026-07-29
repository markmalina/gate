from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0061_voice_dead_letter_reconciliation.py"
    )
    spec = importlib.util.spec_from_file_location("voice_dead_letter_reconciliation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reconciliation_scope_is_exact_and_content_free() -> None:
    migration = _migration()

    migration._require_exact_scope(
        {
            ("telegram_voice_not_inbound", "missing"): 9,
            ("openai_transcription_transport_unknown", "transcribed"): 2,
            ("openai_transcription_transport_unknown", "unknown"): 1,
        }
    )

    assert migration.down_revision == "0060_call_processing_status"
    assert migration._CUTOFF == "2026-07-29T10:20:00+00:00"


def test_reconciliation_stops_on_count_or_archive_state_drift() -> None:
    migration = _migration()

    with pytest.raises(
        RuntimeError,
        match="voice_dead_letter_reconciliation_count_mismatch",
    ):
        migration._require_exact_scope(
            {
                ("telegram_voice_not_inbound", "missing"): 8,
                ("openai_transcription_transport_unknown", "unknown"): 3,
            }
        )

    with pytest.raises(
        RuntimeError,
        match="voice_dead_letter_reconciliation_state_mismatch",
    ):
        migration._require_exact_scope(
            {
                ("telegram_voice_not_inbound", "rejected"): 9,
                ("openai_transcription_transport_unknown", "unknown"): 3,
            }
        )


def test_reconciliation_uses_one_atomic_queue_transition_and_one_recovery_grant() -> None:
    root = Path(__file__).parents[1]
    migration = (
        root / "alembic" / "versions" / "0061_voice_dead_letter_reconciliation.py"
    ).read_text(encoding="utf-8")
    worker = (root / "app" / "worker" / "dropbox_calls.py").read_text(
        encoding="utf-8"
    )

    assert "WITH eligible AS MATERIALIZED" in migration
    assert "jsonb_typeof(r.payload -> 'business_message' -> 'voice')" in migration
    assert "transcription_recovery_authorized_at = COALESCE" in migration
    assert "telegram_voice_recovery_reconcile" not in migration
    assert "async def _requeue_recovered_telegram_voice" in worker
    assert "result.outcome is TelegramVoiceOutcome.TRANSCRIBED" in worker
    assert 'expected_error = "openai_transcription_transport_unknown"' in worker
