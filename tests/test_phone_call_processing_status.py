from pathlib import Path


def test_phone_call_processing_status_is_migrated_with_claim_code() -> None:
    root = Path(__file__).parents[1]
    migration = (
        root / "alembic" / "versions" / "0060_phone_call_processing_status.py"
    ).read_text(encoding="utf-8")
    worker = (root / "app" / "worker" / "dropbox_calls.py").read_text(
        encoding="utf-8"
    )

    assert '_NEW_STATUSES = (*_OLD_STATUSES, "processing")' in migration
    assert 'WHERE transcription_status = \'processing\'' in migration
    assert 'claimed.transcription_status = "processing"' in worker
    assert 'claimed.transcription_reason_code = "dropbox_call_processing"' in worker
