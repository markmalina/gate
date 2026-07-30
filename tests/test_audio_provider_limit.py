# ruff: noqa: I001
import asyncio
from pathlib import Path

import pytest

from app.application.telegram_voice_transcription import (
    OPENAI_TRANSCRIPTION_LIMIT_BYTES,
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
    DownloadedVoice,
    TelegramVoiceError,
)
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer


def test_provider_oversized_native_audio_is_split_into_ordered_parts() -> None:
    calls: list[int] = []

    async def chunked(data: bytes) -> tuple[int, tuple[bytes, ...], bytes]:
        calls.append(len(data))
        return 0, (b"part-one", b"part-two"), b""

    normalizer = TelegramVoiceNormalizer(chunked_runner=chunked)
    source = DownloadedVoice(
        data=b"x" * (OPENAI_TRANSCRIPTION_LIMIT_BYTES + 1),
        filename="call.m4a",
        media_type="audio/mp4",
    )

    result = asyncio.run(normalizer.normalize(source))

    assert calls == [OPENAI_TRANSCRIPTION_LIMIT_BYTES + 1]
    assert result.data == b"part-one"
    assert result.filename == "telegram-voice-part-001.mp3"
    assert result.media_type == "audio/mpeg"
    assert len(result.additional_parts) == 1
    assert result.additional_parts[0].data == b"part-two"
    assert result.additional_parts[0].filename == "telegram-voice-part-002.mp3"


def test_native_audio_at_provider_limit_is_not_reencoded() -> None:
    async def unexpected(
        _data: bytes,
    ) -> tuple[int, tuple[bytes, ...], bytes]:
        raise AssertionError("chunking must not run")

    normalizer = TelegramVoiceNormalizer(chunked_runner=unexpected)
    source = DownloadedVoice(
        data=b"x" * OPENAI_TRANSCRIPTION_LIMIT_BYTES,
        filename="call.m4a",
        media_type="audio/mp4",
    )

    result = asyncio.run(normalizer.normalize(source))

    assert result.data is source.data
    assert result.filename == "call.m4a"
    assert result.additional_parts == ()


def test_each_chunk_must_finish_under_provider_limit() -> None:
    async def still_large(
        _data: bytes,
    ) -> tuple[int, tuple[bytes, ...], bytes]:
        return 0, (b"x" * (OPENAI_TRANSCRIPTION_LIMIT_BYTES + 1),), b""

    normalizer = TelegramVoiceNormalizer(chunked_runner=still_large)
    source = DownloadedVoice(
        data=b"x" * (OPENAI_TRANSCRIPTION_LIMIT_BYTES + 1),
        filename="call.m4a",
        media_type="audio/mp4",
    )

    with pytest.raises(TelegramVoiceError, match="telegram_voice_normalized_too_large"):
        asyncio.run(normalizer.normalize(source))


def test_historical_call_fits_archive_but_requires_chunking() -> None:
    size = 25_505_021
    assert OPENAI_TRANSCRIPTION_LIMIT_BYTES < size <= TELEGRAM_DOWNLOAD_LIMIT_BYTES


def test_chunking_uses_bounded_duration_and_mp3() -> None:
    source = (
        Path(__file__).parents[1]
        / "app"
        / "infrastructure"
        / "telegram_voice.py"
    ).read_text(encoding="utf-8")

    assert '"32k"' in source
    assert '"180"' in source
    assert '"segment"' in source
    assert '"mp3"' in source


def test_dropbox_calls_are_claimed_atomically_before_provider_work() -> None:
    source = (
        Path(__file__).parents[1]
        / "app"
        / "worker"
        / "dropbox_calls.py"
    ).read_text(encoding="utf-8")

    assert ".with_for_update(skip_locked=True)" in source
    assert 'claimed.transcription_status = "processing"' in source
    assert 'reservation_key=f"dropbox-call-v12:' in source
    assert source.count("== _LONG_CALL_RECOVERY_STATUS") == 3
    assert '_LONG_CALL_RECOVERY_MODEL = "gpt-4o-transcribe"' in source
    assert "model=transcription_model" in source
    assert "stored.transcription_model = transcription_model" in source


def test_long_call_is_requeued_by_chunking_migration() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0058_long_call_chunking.py"
    ).read_text(encoding="utf-8")

    assert "a1a82247-434e-4f43-8974-35a972ba9dc2" in migration
    assert "openai_transcription_rejected_http_400" in migration
    assert "size_bytes = 25505021" in migration



def test_rejected_long_call_is_requeued_by_wav_recovery_migration() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0063_rejected_long_call_wav_retry.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0064_long_call_wav_retry"' in migration
    assert 'down_revision: str | None = "0063_voice_unknown_retry"' in migration
    assert "a1a82247-434e-4f43-8974-35a972ba9dc2" in migration
    assert "openai_transcription_rejected_http_400" in migration
    assert "_EXPECTED_SIZE = 25_505_021" in migration
    assert "octet_length(data) = :expected_size" in migration



def test_long_call_recovery_uses_deploy_safe_claim_status() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0064_long_call_claim_v8.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0065_long_call_claim_v8"' in migration
    assert 'down_revision: str | None = "0064_long_call_wav_retry"' in migration
    assert "a1a82247-434e-4f43-8974-35a972ba9dc2" in migration
    assert "recovery_v8" in migration
    assert "ck_phone_call_recordings_transcription_status" in migration
    assert "_RECOVERY_STATUSES = (*_PRE_RECOVERY_STATUSES, _RECOVERY_STATUS)" in migration
    upgrade = migration.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert upgrade.count("op.drop_constraint(_CONSTRAINT") == 1
    assert upgrade.count("op.create_check_constraint(") == 1
    assert upgrade.index("op.create_check_constraint(") < upgrade.index("result = bind.execute")
    assert downgrade.count("op.drop_constraint(_CONSTRAINT") == 1
    assert downgrade.count("op.create_check_constraint(") == 1
    assert downgrade.index("bind.execute") < downgrade.index("op.drop_constraint(_CONSTRAINT")
    assert "billing_reservation_replayed" in migration
    assert "_EXPECTED_SIZE = 25_505_021" in migration



def test_five_minute_long_call_recovery_uses_isolated_v9_claim() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0065_long_call_claim_v9.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0066_long_call_claim_v9"' in migration
    assert 'down_revision: str | None = "0065_long_call_claim_v8"' in migration
    assert "a1a82247-434e-4f43-8974-35a972ba9dc2" in migration
    assert "recovery_v9" in migration
    assert "recovery_v8" in migration
    assert "openai_transcription_rejected_http_400" in migration
    assert "ck_phone_call_recordings_transcription_status" in migration
    upgrade = migration.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert upgrade.index("op.create_check_constraint(") < upgrade.index("result = bind.execute")
    assert downgrade.index("bind.execute") < downgrade.index("op.drop_constraint(_CONSTRAINT")



def test_three_minute_long_call_recovery_uses_isolated_v10_claim() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0066_long_call_claim_v10.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0067_long_call_claim_v10"' in migration
    assert 'down_revision: str | None = "0066_long_call_claim_v9"' in migration
    assert "a1a82247-434e-4f43-8974-35a972ba9dc2" in migration
    assert "recovery_v10" in migration
    assert "recovery_v9" in migration
    assert "openai_transcription_rejected_http_400" in migration
    assert "ck_phone_call_recordings_transcription_status" in migration
    upgrade = migration.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert upgrade.index("op.create_check_constraint(") < upgrade.index("result = bind.execute")
    assert downgrade.index("bind.execute") < downgrade.index("op.drop_constraint(_CONSTRAINT")



def test_three_minute_mp3_long_call_recovery_uses_isolated_v11_claim() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0067_long_call_claim_v11.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0068_long_call_claim_v11"' in migration
    assert 'down_revision: str | None = "0067_long_call_claim_v10"' in migration
    assert "a1a82247-434e-4f43-8974-35a972ba9dc2" in migration
    assert "recovery_v11" in migration
    assert "recovery_v10" in migration
    assert "openai_transcription_rejected_http_400" in migration
    assert "ck_phone_call_recordings_transcription_status" in migration
    upgrade = migration.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert upgrade.index("op.create_check_constraint(") < upgrade.index("result = bind.execute")
    assert downgrade.index("bind.execute") < downgrade.index("op.drop_constraint(_CONSTRAINT")



def test_full_model_long_call_recovery_uses_isolated_v12_claim() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0068_long_call_claim_v12.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0069_long_call_claim_v12"' in migration
    assert 'down_revision: str | None = "0068_long_call_claim_v11"' in migration
    assert "a1a82247-434e-4f43-8974-35a972ba9dc2" in migration
    assert "recovery_v12" in migration
    assert "recovery_v11" in migration
    assert "openai_transcription_rejected_http_400" in migration
    assert "ck_phone_call_recordings_transcription_status" in migration
    upgrade = migration.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert upgrade.index("op.create_check_constraint(") < upgrade.index("result = bind.execute")
    assert downgrade.index("bind.execute") < downgrade.index("op.drop_constraint(_CONSTRAINT")
