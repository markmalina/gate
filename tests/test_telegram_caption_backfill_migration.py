from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0099_telegram_caption_backfill.py"
)


def test_caption_backfill_migration_is_bounded_and_null_only() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    compile(source, str(MIGRATION), "exec")
    assert 'revision = "0099_telegram_caption_backfill"' in source
    assert 'down_revision = "0098_beta_space_search"' in source

    for table in (
        "telegram_receipts",
        "telegram_updates",
        "canonical_messages",
        "messages",
        "gate_events",
    ):
        assert table in source

    assert "{business_message,caption}" in source
    assert "{message,caption}" in source
    assert "payload #>>" in source
    assert "btrim" in source
    assert source.count("IS NULL") >= 4
    assert "Forward-only data repair" in source
