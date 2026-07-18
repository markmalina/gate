"""Migration guards for the canonical Business text cutover link."""

import importlib.util
from pathlib import Path


def test_business_text_cutover_migration_is_additive_and_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0038_business_text_canonical_cutover.py"
    )
    spec = importlib.util.spec_from_file_location("business_text_cutover", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0038_business_text_cutover"
    assert len(module.revision) <= 32
    assert module.down_revision == "0037_direction_parity_v2"
    source = path.read_text(encoding="utf-8")
    assert "canonical_message_id" in source
    assert "nullable=True" in source
    assert "uq_messages_canonical_message_id" in source
    assert callable(module.upgrade)
    assert callable(module.downgrade)
