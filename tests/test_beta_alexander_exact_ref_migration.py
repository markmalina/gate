"""Release guards for resolving Alexander by canonical installation ref."""

import importlib.util
from pathlib import Path


def test_beta_alexander_exact_ref_binding_is_fail_closed() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0052_beta_alexander_exact_ref.py"
    )
    spec = importlib.util.spec_from_file_location(
        "beta_alexander_exact_ref",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0052_beta_alexander_exact_ref"
    assert module.down_revision == "0051_beta_alexander_model"
    assert module._INSTALLATION_REF == (
        "installation-7fbf02145be55011ae8307e57dd8216c"
    )
    assert module._BETA_GROUP_ID == "beta-group-stroi-alphav-001"
    assert module._TARGET_DEPLOYMENT_KEY == "alpha-model-v2-openai"
    assert module._TARGET_REVISION_KEY == "alpha-model-v2"

    source = path.read_text(encoding="utf-8")
    assert "installation.installation_key = :installation_ref" in source
    assert "FOR UPDATE OF installation" in source
    assert "FROM beta_context_bindings" in source
    assert "beta_alexander_context_binding_conflict" in source
    assert "beta_alexander_exact_nonterminal_turn_exists" in source
    assert "beta_alexander_exact_unused_grant_exists" in source
    assert "AND state = 'paused'" in source
    assert source.count("UPDATE agent_installations") == 1
    assert "DELETE FROM beta_context_bindings" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
