"""Static deployment wiring tests for Railway readiness."""

import tomllib
from pathlib import Path
from typing import Any


def _configuration() -> dict[str, Any]:
    return tomllib.loads(Path("railway.toml").read_text(encoding="utf-8"))


def test_railway_uses_strict_readiness_after_single_head_migration() -> None:
    configuration = _configuration()

    assert configuration["deploy"]["preDeployCommand"] == "python -m app.worker.reply_cutover"
    assert configuration["deploy"]["healthcheckPath"] == "/ready"


def test_staging_start_command_isolated_from_production_aqub() -> None:
    configuration = _configuration()
    staging_command = configuration["environments"]["staging"]["deploy"]["startCommand"]

    assert staging_command == (
        "/bin/sh -c 'exec uvicorn app.main:app --host 0.0.0.0 "
        "--port ${PORT:-8000}'"
    )
    assert "aqub-production" not in staging_command
    assert "GATE_AQUB_MEDIA_FORWARD_ENABLED=true" not in staging_command
    assert "GATE_AQUB_FOOD_MEDIA_PROCESSING_ENABLED=true" not in staging_command
    assert "GATE_AQUB_FOOD_MEDIA_DATA_GATE_APPROVED=true" not in staging_command
