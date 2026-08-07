from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.infrastructure.beta_space_checkpoint_batches import (
    _portable_wedding_event,
)


def test_wedding_checkpoint_projection_keeps_text_and_redacts_transport_ids() -> None:
    binding = SimpleNamespace(
        installation_ref="installation-4de1df8a77f8468d8b30e8a41db876aa",
        storage_ref="beta-group-wedding-minsk-001",
    )
    event = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000123"),
        checkpoint_sequence=7,
        media_kind="text",
        telegram_user_id="99887766",
        occurred_at=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        created_at=datetime(2026, 8, 7, 20, 0, 1, tzinfo=UTC),
    )
    receipt = SimpleNamespace(
        payload={
            "message": {
                "text": "Согласовали площадку на сорок гостей.",
                "chat": {"id": -100123456789},
                "from": {"id": 99887766},
            }
        }
    )

    projected = _portable_wedding_event(
        binding=binding,
        event=event,
        receipt=receipt,
        voice=None,
    )

    encoded = json.dumps(projected, ensure_ascii=False)
    assert projected["sequence"] == 7
    assert projected["content"]["text"] == "Согласовали площадку на сорок гостей."
    assert "99887766" not in encoded
    assert "-100123456789" not in encoded
    assert projected["actor_ref"].startswith("actor-")
    assert projected["provenance"]["source_event_ref"].startswith("gate-event-")


def test_wedding_voice_checkpoint_contains_transcript_and_structure_only() -> None:
    binding = SimpleNamespace(
        installation_ref="installation-4de1df8a77f8468d8b30e8a41db876aa",
        storage_ref="beta-group-wedding-minsk-001",
    )
    event = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000124"),
        checkpoint_sequence=8,
        media_kind="voice",
        telegram_user_id="11223344",
        occurred_at=datetime(2026, 8, 7, 20, 1, tzinfo=UTC),
        created_at=datetime(2026, 8, 7, 20, 1, 1, tzinfo=UTC),
    )
    receipt = SimpleNamespace(
        payload={
            "message": {
                "voice": {"file_id": "private-provider-file-id"},
                "chat": {"id": -100123456789},
            }
        }
    )
    voice = SimpleNamespace(
        transcript_text="Нужно подготовить список гостей.",
        structured_text="Задачи: подготовить список гостей.",
    )

    projected = _portable_wedding_event(
        binding=binding,
        event=event,
        receipt=receipt,
        voice=voice,
    )

    encoded = json.dumps(projected, ensure_ascii=False)
    assert "Нужно подготовить список гостей." in projected["content"]["text"]
    assert "Задачи: подготовить список гостей." in projected["content"]["text"]
    assert "private-provider-file-id" not in encoded
    assert "-100123456789" not in encoded
