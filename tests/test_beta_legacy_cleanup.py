from __future__ import annotations

from pathlib import Path

from app.models.beta_wedding_voice import (
    BetaWeddingVoiceDeliveryModel,
    BetaWeddingVoiceJobModel,
)

ROOT = Path(__file__).resolve().parents[1]
DEAD_RUNTIME_PATHS = (
    "app/application/beta_wedding_voice.py",
    "app/infrastructure/beta_wedding_voice.py",
    "app/infrastructure/beta_wedding_voice_provider.py",
)
UNIVERSAL_RUNTIME_PATHS = (
    "app/infrastructure/beta_material.py",
    "app/worker/beta_space.py",
)


def test_unreachable_wedding_voice_runtime_is_absent() -> None:
    assert all(not (ROOT / path).exists() for path in DEAD_RUNTIME_PATHS)


def test_historical_models_and_universal_runtime_are_preserved() -> None:
    assert BetaWeddingVoiceJobModel.__tablename__ == "beta_wedding_voice_jobs"
    assert (
        BetaWeddingVoiceDeliveryModel.__tablename__
        == "beta_wedding_voice_deliveries"
    )
    assert all((ROOT / path).is_file() for path in UNIVERSAL_RUNTIME_PATHS)
