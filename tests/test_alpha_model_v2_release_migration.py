"""Release guards for immutable Alpha model v2."""

import importlib.util
from pathlib import Path


def test_alpha_model_v2_release_is_exact_reversible_and_keeps_v1() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0034_alpha_model_v2_release.py"
    )
    spec = importlib.util.spec_from_file_location("alpha_model_v2_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0034_alpha_model_v2_release"
    assert module.down_revision == "0033_canonical_input_origin"
    assert module._PREVIOUS_DEPLOYMENT_KEY == "alpha-model-v1-openai"
    assert module._REVISION_KEY == "alpha-model-v2"
    assert module._DEPLOYMENT_KEY == "alpha-model-v2-openai"
    assert module._SOURCE_COMMIT_SHA == (
        "0675e284e2a5b1a4b1e7255a033d5b1f2aa06ec3"
    )
    assert module._CONTENT_SHA256 == (
        "ad3a1ee421358afb2e137841c0dfec7a8e83fe17688e6916ad818c923e14fb00"
    )
    assert module._RUNTIME_PROFILE == "openai-responses-v1"
    assert module._MODEL_PROFILE == "gpt-5.6-luna"
    source = path.read_text(encoding="utf-8")
    assert "UPDATE agent_installations" not in source
    assert "DELETE FROM agent_installations" not in source
    assert "SET state = 'paused'" in source
    assert "SET state = 'staged'" in source
    assert "alpha_model_preflights" in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
