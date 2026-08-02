from pathlib import Path

from app.models.become import BecomeControlledIntakeReceiptModel


def test_become_controlled_intake_model_is_content_free_and_owner_only() -> None:
    columns = set(BecomeControlledIntakeReceiptModel.__table__.columns.keys())
    assert {
        "source_receipt_id",
        "receipt_key",
        "receipt_digest",
        "installation_ref",
        "subject_ref",
        "update_ref",
        "group_state",
        "member_count",
        "payload",
    } <= columns
    for forbidden in (
        "provider_dialog_id",
        "provider_actor_id",
        "chat_id",
        "telegram_id",
        "message_text",
        "transcript",
        "media_bytes",
        "token",
        "secret",
    ):
        assert forbidden not in columns


def test_become_controlled_intake_migration_chains_current_head() -> None:
    root = Path(__file__).parents[1]
    migration = (
        root / "alembic" / "versions" / "0071_become_controlled_intake.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "0071_become_controlled_intake"' in migration
    assert 'down_revision: str | None = "0070_long_call_claim_v13"' in migration
    assert "group_state = 'owner_only' AND member_count = 2" in migration
    assert "telegram_receipts.id" in migration
    assert "agent_installations.id" in migration
