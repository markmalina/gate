"""Gate-side verification of the shared AQUB Telegram command contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.application.aqub_live import _candidate_input_allowed
from app.application.aqub_s0_preflight import AQUBPreflightInbound

FIXTURE = Path("contracts/telegram-commands.v1.json")
CANDIDATE_BASES = {"/status", "/program", "/confirm_program"}


def _inbound(text: str) -> AQUBPreflightInbound:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    return AQUBPreflightInbound(
        bot_username="@AQubAgentBot",
        external_id="1",
        chat_id="-1001234567890",
        actor_id="987654321",
        command=text,
        payload={
            "update_id": 1,
            "message": {
                "message_id": 2,
                "date": int(now.timestamp()),
                "chat": {"id": -1001234567890, "type": "supergroup"},
                "from": {"id": 987654321, "is_bot": False},
                "text": text,
            },
        },
        occurred_at=now,
        received_at=now,
    )


def test_shared_command_fixture_schema_and_candidate_boundary() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == "aqub.telegram-commands.v1"
    assert fixture["cases"]

    for case in fixture["cases"]:
        text = case["text"]
        command_base = case["command_base"]
        expected_candidate = (
            command_base in CANDIDATE_BASES
            or command_base.startswith("/ace_join_")
        )
        assert _candidate_input_allowed(_inbound(text)) is expected_candidate
