"""Release guards for the exact Alexander model binding."""

import importlib.util
from pathlib import Path


def test_beta_alexander_model_binding_is_exact_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0051_beta_alexander_model.py"
    )
    spec = importlib.util.spec_from_file_location(
        "beta_alexander_model_binding",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0051_beta_alexander_model"
    assert module.down_revision == "0050_beta_context_pilot"
    assert module._INSTALLATION_ID == "7fbf0214-5be5-5011-ae83-07e57dd8216c"
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
    assert "FOR UPDATE OF installation" in source
    assert "beta_alexander_installation_ambiguous" in source
    assert "beta_alexander_nonterminal_turn_exists" in source
    assert "beta_alexander_unused_grant_exists" in source
    assert "AND state = 'paused'" in source
    assert source.count("UPDATE agent_installations") == 1
    assert "INSERT INTO agent_installations" not in source
    assert "DELETE FROM agent_installations" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
