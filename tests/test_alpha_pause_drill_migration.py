"""Release guards for the reversible fixed-canary pause drill."""

import importlib.util
from pathlib import Path


def test_alpha_pause_drill_is_exact_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0024_pause_alpha_canary_drill.py"
    )
    spec = importlib.util.spec_from_file_location("alpha_pause_drill_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0024_pause_alpha_canary_drill"
    assert module.down_revision == "0023_alpha_model_release"
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
