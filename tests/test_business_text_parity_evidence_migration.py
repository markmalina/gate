"""Migration guards for Business text parity evidence."""

import importlib.util
from pathlib import Path


def test_business_text_parity_evidence_migration_is_additive_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0036_business_text_parity_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("business_text_parity_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0036_business_text_parity"
    assert len(module.revision) <= 32
    assert module.down_revision == "0035_alpha_rate_limit_deferral"
    source = path.read_text(encoding="utf-8")
    assert "telegram_business_text_parity_evidence" in source
    assert "canonical_message_id" in source
    assert "workspace_id" in source
    assert "account_id" in source
    assert "text_content" not in source
    assert "provider_dialog_id" not in source
    assert "business_connection_id" not in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
