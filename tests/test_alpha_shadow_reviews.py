"""Privacy and product contracts for the Alpha shadow review room."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.alpha_shadow_reviews import alpha_shadow_reviews_payload
from app.infrastructure.alpha_shadow_reviews import list_alpha_shadow_reviews


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows(self.rows)


class _Turn:
    id = UUID("11111111-1111-1111-1111-111111111111")
    status = "proposed"
    result_kind = "reply"
    result_code = "alpha_proposed"
    candidate_text = "Короткий ответ Alpha"
    candidate_sha256 = "a" * 64
    context_message_ids = ["m1", "m2"]
    created_at = datetime(2026, 7, 16, 5, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_review_room_is_group_scoped_and_provider_id_free() -> None:
    session = _Session(
        [
            (
                _Turn(),
                "alpha-model-v1-openai",
                "installation-opaque-ref",
                "Alpha Group",
                "/alpha Что дальше?",
            )
        ]
    )

    reviews = await list_alpha_shadow_reviews(
        session,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        limit=100,
    )
    payload = alpha_shadow_reviews_payload(reviews)

    assert len(session.statements) == 1
    assert payload["count"] == 1
    assert payload["telegram_delivery_blocked"] is True
    assert payload["provider_dialog_ids_exposed"] is False
    item = payload["items"][0]
    assert item["group_label"] == "Alpha Group"
    assert item["installation_ref"] == "installation-opaque-ref"
    assert item["trigger_text"] == "/alpha Что дальше?"
    assert item["candidate_text"] == "Короткий ответ Alpha"
    assert item["context_message_count"] == 2
    assert item["sent_to_telegram"] is False
    assert "provider_dialog_id" not in str(payload["items"])
    assert "provider_message_id" not in str(payload["items"])
    assert "workspace_key" not in str(payload["items"])
