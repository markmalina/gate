from pathlib import Path


def test_personal_s3_operation_permit_migration_is_one_shot_and_reversible() -> None:
    migration = Path("alembic/versions/0127_personal_s3_operation_permit.py").read_text(
        encoding="utf-8"
    )
    revision = "0127_s3_operation_permit"
    assert f'revision: str = "{revision}"' in migration
    assert len(revision) <= 32
    assert 'down_revision: str | None = "0126_personal_standalone_fence"' in migration
    for column in (
        "permit_approval_ref",
        "permit_source_revision",
        "permit_text_sha256",
        "permit_expires_at",
    ):
        assert migration.count(f'Column("{column}"') == 1
        assert f'drop_column("personal_command_operations", "{column}")' in migration
    assert "uq_personal_command_s3_permit_approval" in migration
    assert "ck_personal_command_s3_permit_bundle" in migration
    assert "state IN ('running', 'succeeded', 'unknown')" in migration
