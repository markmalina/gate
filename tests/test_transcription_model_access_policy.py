import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_transcription_model_access_lock_is_accepted_and_bounded() -> None:
    lock = json.loads((ROOT / "model-access.lock").read_text(encoding="utf-8"))

    assert lock["status"] == "accepted"
    assert lock["skill"]["name"] == "use-model-access"
    assert lock["skill"]["id"] == "6a8c4f8397088191bdda343824817fe2"
    assert lock["project_policy"] == {
        "path": "docs/policies/GATE_TRANSCRIPTION_MODEL_ACCESS_POLICY.md",
        "version": "gate-transcription-model-access-v2",
    }
    assert lock["feature_default"] is False
    assert lock["endpoint"] == "https://api.openai.com/v1/audio/transcriptions"
    assert lock["models"] == [
        "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe",
    ]
    assert lock["automatic_retry_count"] == 0
    assert lock["max_source_bytes"] == 100 * 1024 * 1024
    assert lock["max_part_bytes"] == 25_000_000
    assert lock["max_parts"] == 20
    assert lock["monthly_hard_limit_microusd"] == 3_000_000
    assert lock["retention"]["zero_data_retention_claimed"] is False


def test_transcription_policy_matches_runtime_constants() -> None:
    application = (
        ROOT / "app" / "application" / "telegram_voice_transcription.py"
    ).read_text(encoding="utf-8")
    worker = (ROOT / "app" / "worker" / "dropbox_calls.py").read_text(
        encoding="utf-8"
    )
    adapter = (
        ROOT / "app" / "infrastructure" / "openai_transcription.py"
    ).read_text(encoding="utf-8")

    assert "MAX_SOURCE_AUDIO_BYTES = 100 * 1024 * 1024" in application
    assert "MAX_TRANSCRIPTION_PARTS = 20" in application
    assert "OPENAI_TRANSCRIPTION_LIMIT_BYTES = 25_000_000" in application
    assert "DROPBOX_CALL_DOWNLOAD_LIMIT_BYTES = MAX_SOURCE_AUDIO_BYTES" in worker
    assert '"gpt-4o-mini-transcribe"' in adapter
    assert '"gpt-4o-transcribe"' in adapter
    assert "timeout_seconds: float = 60" in adapter
