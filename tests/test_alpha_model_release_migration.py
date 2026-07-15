"""Release guards for the staged-only Alpha model deployment."""

import importlib.util
from pathlib import Path


def test_alpha_model_release_is_exact_reversible_and_staged_only() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0023_alpha_model_release.py"
    )
    spec = importlib.util.spec_from_file_location("alpha_model_release_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0023_alpha_model_release"
    assert module.down_revision == "0022_alpha_bundle_digest"
    assert module._REVISION_KEY == "alpha-model-v1"
    assert module._DEPLOYMENT_KEY == "alpha-model-v1-openai"
    assert module._SOURCE_COMMIT_SHA == (
        "7c2781602448ac140e1e31ea27cb93414e3333d2"
    )
    assert module._CONTENT_SHA256 == (
        "bdb610fd54e5444e9ed22df035418c6ba2217a620d427c2ae49a51adef917dff"
    )
    assert module._RUNTIME_PROFILE == "openai-responses-v1"
    assert module._MODEL_PROFILE == "gpt-5.6-luna"
    assert module._DEPLOYMENT_STATE == "staged"
    source = path.read_text(encoding="utf-8")
    assert "UPDATE agent_installations" not in source
    assert "DELETE FROM agent_installations" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
