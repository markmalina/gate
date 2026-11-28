"""Static safety contract for the standalone shared-fence migration."""

from pathlib import Path


def test_standalone_fence_migration_is_linear_typed_and_fail_closed() -> None:
    migration = Path("alembic/versions/0126_personal_standalone_fence.py").read_text(
        encoding="utf-8"
    )
    assert 'down_revision: str | None = "0125_personal_runtime_check"' in migration
    assert "physical_peer_key" in migration
    assert "conversation.chat_id" in migration
    assert "ck_personal_send_fence_optional_draft" in migration
    assert "standalone personal Telegram fences require reconciliation" in migration
    downgrade = migration.split("def downgrade()", 1)[1]
    assert "DELETE FROM personal_telegram_send_fences" not in downgrade
