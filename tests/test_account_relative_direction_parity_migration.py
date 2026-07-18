"""Migration guards for the account-relative direction parity epoch."""

import importlib.util
from pathlib import Path


def test_direction_parity_epoch_migration_is_additive_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0037_account_relative_direction_parity.py"
    )
    spec = importlib.util.spec_from_file_location("direction_parity_epoch", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0037_direction_parity_v2"
    assert len(module.revision) <= 32
    assert module.down_revision == "0036_business_text_parity"
    source = path.read_text(encoding="utf-8")
    assert "direction_policy_version" in source
    assert "server_default=sa.text(\"1\")" in source
    assert "server_default=sa.text(\"2\")" in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
