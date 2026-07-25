"""Release guards for the exact Alpha model v2 bounded-live promotion."""

import importlib.util
from pathlib import Path


def test_alpha_model_v2_bounded_live_promotion_is_exact_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0040_alpha_model_v2_bounded_live.py"
    )
    spec = importlib.util.spec_from_file_location(
        "alpha_model_v2_bounded_live",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0040_alpha_model_v2_bounded_live"
    assert module.down_revision == "0039_voice_archive"
    assert module._WORKSPACE_KEY == "mark-personal"
    assert module._AGENT_KEY == "alpha_group"
    assert module._REVISION_KEY == "alpha-model-v2"
    assert module._DEPLOYMENT_KEY == "alpha-model-v2-openai"
    assert module._RUNTIME_PROFILE == "openai-responses-v1"
    assert module._MODEL_PROFILE == "gpt-5.6-luna"
    assert module._POLICY_VERSION == "alpha-model-v2"

    source = path.read_text(encoding="utf-8")
    assert "alpha_model_v2_preflight_not_exact" in source
    assert "alpha_model_v2_nonterminal_turn_exists" in source
    assert "SET state = 'active'" in source
    assert "SET state = 'staged'" in source
    assert "UPDATE agent_installations" not in source
    assert "DELETE FROM agent_installations" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
