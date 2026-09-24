"""The owner-approved cap changes only the exact Gate voice entitlement."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/0109_gate_voice_three_dollar_cap.py"
)


def test_migration_targets_only_owner_gate_voice_scope_and_is_reversible():
    spec = importlib.util.spec_from_file_location("gate_voice_cap", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    module.op = SimpleNamespace(get_bind=lambda: SimpleNamespace(
        execute=lambda statement: statements.append(str(statement))
    ))
    module.upgrade()
    module.downgrade()
    assert len(statements) == 2
    for sql in statements:
        assert "w.key = 'mark-personal'" in sql
        assert "w.is_active IS TRUE" in sql
        assert "entitlement_key = 'gate-telegram-voice-v1'" in sql
        assert "scope_kind = 'telegram_connector'" in sql
        assert "scope_ref = 'telegram_business'" in sql
        assert "state = 'active'" in sql
        assert "valid_until IS NULL" in sql
        assert "billing_budget_reservations" not in sql
    assert "SET hard_limit_microusd = 3000000" in statements[0]
    assert "hard_limit_microusd = 1000000" in statements[0]
    assert "SET hard_limit_microusd = 1000000" in statements[1]
    assert "hard_limit_microusd = 3000000" in statements[1]
