"""Release-gate guards for the exact Alpha connector activation."""

import importlib.util
from pathlib import Path


def test_alpha_connector_release_is_reversible_and_owner_gated() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0021_enable_alpha_canary.py"
    )
    spec = importlib.util.spec_from_file_location("enable_alpha_canary_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0021_enable_alpha_canary"
    assert module.down_revision == "0020_alpha_canary_release"
    assert len(module._REQUIRED_OWNER_FLAGS) == 6
    assert all(module._enabled(value) for value in ("1", "true", "YES", " on "))
    assert not any(module._enabled(value) for value in (None, "", "0", "false"))
    assert callable(module.upgrade)
    assert callable(module.downgrade)
