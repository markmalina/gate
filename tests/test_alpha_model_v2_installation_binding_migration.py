"""Release guards for the exact Alpha model v2 installation binding."""

import importlib.util
from pathlib import Path


def test_alpha_model_v2_installation_binding_is_exact_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0041_alpha_model_v2_installation_binding.py"
    )
    spec = importlib.util.spec_from_file_location(
        "alpha_model_v2_installation_binding",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0041_alpha_model_v2_installation_binding"
    assert module.down_revision == "0040_alpha_model_v2_bounded_live"
    assert module._WORKSPACE_KEY == "mark-personal"
    assert module._CONNECTOR_KEY == "telegram_alpha_group"
    assert module._AGENT_KEY == "alpha_group"
    assert module._FIXTURE_DEPLOYMENT_KEY == "alpha-canary-v1-fixture"
    assert module._FIXTURE_REVISION_KEY == "alpha-canary-v1"
    assert module._TARGET_DEPLOYMENT_KEY == "alpha-model-v2-openai"
    assert module._TARGET_REVISION_KEY == "alpha-model-v2"
    assert module._TARGET_RUNTIME_PROFILE == "openai-responses-v1"
    assert module._TARGET_MODEL_PROFILE == "gpt-5.6-luna"
    assert module._TARGET_POLICY_VERSION == "alpha-model-v2"

    source = path.read_text(encoding="utf-8")
    assert "FOR UPDATE OF i" in source
    assert "alpha_model_v2_installation_binding_ambiguous" in source
    assert "alpha_installation_nonterminal_turn_exists" in source
    assert "alpha_installation_unused_grant_exists" in source
    assert "AND state = 'active'" in source
    assert source.count("UPDATE agent_installations") == 2
    assert "INSERT INTO agent_installations" not in source
    assert "DELETE FROM agent_installations" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
