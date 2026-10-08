"""Snapshot integrity and authenticated event classification, provider-free."""

from copy import deepcopy

import pytest
from app.application.dialog_snapshots import (
    snapshot_digest,
    snapshot_matches,
    telegram_dialog_key,
)


def test_snapshot_binds_scope_version_and_projected_message() -> None:
    state = {"workspace": "synthetic", "account": "a", "project": "p",
             "chat": "1", "revision": 1, "text": "same"}
    original = snapshot_digest(state)
    assert snapshot_matches(original, snapshot_digest(deepcopy(state)))
    for field in state:
        changed = {**state, field: str(state[field]) + "changed"}
        assert not snapshot_matches(original, snapshot_digest(changed))
    for invalid in (None, True, {}, "", original.upper(), "dialog-v1:" + "g" * 64):
        assert not snapshot_matches(invalid, original)


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
