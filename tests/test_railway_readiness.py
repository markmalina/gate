"""Static deployment wiring tests for Railway readiness."""

import tomllib
from pathlib import Path


def test_railway_uses_strict_readiness_after_single_head_migration() -> None:
    configuration = tomllib.loads(Path("railway.toml").read_text(encoding="utf-8"))

    assert configuration["deploy"]["preDeployCommand"] == "alembic upgrade head"
    assert configuration["deploy"]["healthcheckPath"] == "/ready"
