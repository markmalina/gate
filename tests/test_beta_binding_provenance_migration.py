"""Static safety contract for the irreversible legacy-provenance decision."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_migration_quarantines_legacy_wedding_and_fails_closed_on_downgrade() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0096_beta_binding_provenance.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | Sequence[str] | None = "0095_beta_checkpoint_wake"' in migration
    assert "provenance_state = 'quarantined'" in migration
    assert "provenance_evidence_kind = 'legacy_mutable_telegram'" in migration
    assert "state = 'quarantined'" in migration
    assert "UPDATE beta_space_bindings SET state = 'revoked'" in migration
    assert "SET state = 'consented'" not in migration.split("def downgrade()", 1)[1]
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    state_constraint_drop = upgrade.index(
        'op.drop_constraint(\n        "ck_beta_space_binding_state"'
    )
    quarantine_update = upgrade.index('"state = \'quarantined\' "')
    assert state_constraint_drop < quarantine_update


def test_migration_module_loads_without_running_database_operations() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0096_beta_binding_provenance.py"
    )
    spec = importlib.util.spec_from_file_location("beta_binding_provenance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0096_beta_binding_provenance"
