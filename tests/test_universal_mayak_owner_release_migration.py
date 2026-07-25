"""Release guards for the exact owner-only Universal Mayak binding."""

import importlib.util
from pathlib import Path


def test_universal_mayak_owner_release_is_exact_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0044_universal_mayak_owner_release.py"
    )
    spec = importlib.util.spec_from_file_location(
        "universal_mayak_owner_release",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0044_mayak_owner_release"
    assert module.down_revision == "0043_alpha_model_v2_binding"
    assert module._WORKSPACE_KEY == "mark-personal"
    assert module._ACCOUNT_ALIAS == "telegram_mark_by"
    assert module._PROJECT_ALIAS == "oko"
    assert module._AGENT_KEY == "oko"
    assert module._REVISION_KEY == "universal-mayak-v1"
    assert module._DEPLOYMENT_KEY == "universal-mayak-v1-openai"
    assert module._RUNTIME_PROFILE == "openai-responses-v1"
    assert module._MODEL_PROFILE == "gpt-5.6-luna"
    assert module._POLICY_VERSION == "universal-mayak-v1"
    assert len(module._SOURCE_COMMIT_SHA) == 40
    assert len(module._CONTENT_SHA256) == 64
    assert len(module.revision) <= 32

    source = path.read_text(encoding="utf-8")
    for guard in (
        "rt.id = :task_id",
        "a.alias = :account_alias",
        "a.owner = :workspace_key",
        "p.alias = :project_alias",
        "cm.provider_sender_id = d.provider_dialog_id",
        "m.sender_id = d.provider_dialog_id",
        "d.dialog_kind = 'private'",
        "c.connector_kind = 'telegram_business'",
        "bc.is_enabled IS TRUE",
        "FOR UPDATE OF rt, d, c",
        "mayak_owner_target_ambiguous",
        "mayak_owner_account_project_conflict",
        "mayak_owner_installation_state_conflict",
        "UPDATE delivery_grants SET revoked_at = now()",
    ):
        assert guard in source
    assert "Ответь ровно двумя словами" not in source
    assert "provider_dialog_id =" not in source
    assert source.count("INSERT INTO agent_installations") == 1
    assert source.count("UPDATE agent_installations") == 2
    assert callable(module.upgrade)
    assert callable(module.downgrade)
