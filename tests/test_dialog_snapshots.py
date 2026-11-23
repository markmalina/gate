"""Last message IDs and authenticated event classification, provider-free."""

from uuid import uuid4

import pytest
from app.application.dialog_snapshots import (
    snapshot_matches,
    telegram_dialog_key,
)


def test_last_message_id_compares_by_identity() -> None:
    assert snapshot_matches("123", "123")
    assert not snapshot_matches("124", "123")
    legacy_id = str(uuid4())
    assert snapshot_matches(legacy_id, legacy_id)
    for invalid in (None, True, 123, {}, "", "0123", "123x"):
        assert not snapshot_matches(invalid, "123")


@pytest.mark.parametrize("kind", [
    "business_message", "edited_business_message", "deleted_business_messages",
])
@pytest.mark.parametrize("content", [
    {"text": "угу"}, {"voice": {}}, {"photo": []}, {"document": {}}, {},
])
def test_any_supported_business_change_has_a_key_before_content_processing(
    kind: str, content: dict,
) -> None:
    payload = {kind: {"business_connection_id": "synthetic", "chat": {"id": 123}, **content}}
    assert telegram_dialog_key(payload) == ("synthetic", "123")


@pytest.mark.parametrize("payload", [
    {}, {"message": {"chat": {"id": 123}}},
    {"business_message": {"business_connection_id": "synthetic", "chat": {"id": True}}},
])
def test_unrelated_or_invalid_updates_do_not_change_a_business_dialog(payload: dict) -> None:
    assert telegram_dialog_key(payload) is None
