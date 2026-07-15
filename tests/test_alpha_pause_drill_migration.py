"""Release guards for the exact reversible fixed-canary pause/resume drill."""

import importlib.util
from pathlib import Path

import pytest

_MIGRATIONS = (
    (
        "0024_pause_alpha_canary_drill.py",
        "0024_pause_alpha_canary_drill",
        "0023_alpha_model_release",
    ),
    (
        "0025_resume_alpha_canary_drill.py",
        "0025_resume_alpha_canary_drill",
        "0024_pause_alpha_canary_drill",
    ),
)


@pytest.mark.parametrize(("filename", "revision", "down_revision"), _MIGRATIONS)
def test_alpha_pause_resume_drill_is_exact_and_reversible(
    filename: str,
    revision: str,
    down_revision: str,
) -> None:
    path = Path(__file__).parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(revision, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == revision
    assert module.down_revision == down_revision
    assert module._WORKSPACE_KEY == "mark-personal"
    assert module._CONNECTOR_KEY == "telegram_alpha_group"
    assert module._DEPLOYMENT_KEY == "alpha-canary-v1-fixture"
    assert module._REVISION_KEY == "alpha-canary-v1"
    assert module._SOURCE_COMMIT_SHA == (
        "0b5295606694c537c2b59915d0c91e789f1cbb51"
    )
    assert module._CONTENT_SHA256 == (
        "558db275ab8424e108aabf06687623a3f37ef6f2ac6ea0cd12eace6962a92bca"
    )
    source = path.read_text(encoding="utf-8")
    assert "SET state = 'paused'" in source
    assert "SET state = 'active'" in source
    assert "UPDATE transport_connectors" not in source
    assert "UPDATE agent_deployments" not in source
    assert "OPENAI" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
