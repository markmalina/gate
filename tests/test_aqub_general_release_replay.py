from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api import mcp_routes
from app.infrastructure import aqub_general_release_replay as replay
from app.models.aqub import AQUBGeneralRoute
from pydantic import SecretStr


class FakeScalarResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[object]:
        return self._values


class FakeSession:
    def __init__(self, scalar_results: list[list[object]]) -> None:
        self._scalar_results = list(scalar_results)
        self.scalar_calls = 0
        self.rollback_calls = 0

    async def scalars(self, _statement: object) -> FakeScalarResult:
        self.scalar_calls += 1
        return FakeScalarResult(self._scalar_results.pop(0))

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _route(group_chat_id: str = "-1001") -> AQUBGeneralRoute:
    now = datetime(2026, 8, 27, 7, 0, tzinfo=UTC)
    return AQUBGeneralRoute(
        id=uuid4(),
        bot_key="telegram_aqub_preflight",
        group_chat_id=group_chat_id,
        actor_user_id="100",
        evidence_receipt_id=uuid4(),
        status="active",
        bound_at=now,
        activated_at=now,
    )


def _receipt(
    *,
    group_chat_id: int = -1001,
    message_id: int,
    occurred_at: int,
    text: str,
) -> object:
    return SimpleNamespace(
        payload={
            "message": {
                "message_id": message_id,
                "date": occurred_at,
                "chat": {"id": group_chat_id, "type": "group"},
                "from": {"id": 200, "is_bot": False},
                "text": text,
            }
        }
    )


def test_release_replay_digest_is_exact() -> None:
    assert (
        replay._candidate_digest(replay.AQUB_GENERAL_RELEASE_REPLAY_MESSAGES)
        == replay.AQUB_GENERAL_RELEASE_REPLAY_CANDIDATE_SHA256
    )


def test_source_selector_uses_the_earlier_message_without_exposing_text() -> None:
    receipts = [
        _receipt(message_id=11, occurred_at=1002, text="?"),
        _receipt(message_id=10, occurred_at=1000, text="synthetic question"),
    ]

    assert replay._select_source_message_id(receipts, group_chat_id="-1001") == 10


def test_source_selector_fails_closed_on_ambiguous_gap() -> None:
    receipts = [
        _receipt(message_id=11, occurred_at=1100, text="newer"),
        _receipt(message_id=10, occurred_at=1000, text="older"),
    ]

    with pytest.raises(
        replay.AQUBGeneralReleaseReplayError,
        match="aqub_general_replay_source_ambiguous",
    ):
        replay._select_source_message_id(receipts, group_chat_id="-1001")


@pytest.mark.anyio
async def test_send_replay_delivers_two_messages_and_replies_only_first() -> None:
    commands: list[object] = []

    class FakeDeliveryService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def deliver(self, command: object) -> dict[str, object]:
            commands.append(command)
            return {
                "ack": {"status": "committed"},
                "terminal": {"status": "sent"},
            }

    session = FakeSession(
        [
            [_route()],
            [
                _receipt(message_id=11, occurred_at=1002, text="?"),
                _receipt(message_id=10, occurred_at=1000, text="synthetic question"),
            ],
        ]
    )
    settings = SimpleNamespace(
        telegram_aqub_bot_token=SecretStr("synthetic-token"),
        gate_aqub_receipt_signing_key=SecretStr("s" * 32),
    )

    result = await replay.send_aqub_general_release_replay(
        session=session,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        http_client=SimpleNamespace(),  # type: ignore[arg-type]
        installation_ref=replay.AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF,
        turn_ref=replay.AQUB_GENERAL_RELEASE_REPLAY_TURN_REF,
        expected_candidate_sha256=replay.AQUB_GENERAL_RELEASE_REPLAY_CANDIDATE_SHA256,
        confirmed=True,
        delivery_service_factory=FakeDeliveryService,  # type: ignore[arg-type]
    )

    assert result["state"] == "sent"
    assert result["replay"] is False
    assert len(commands) == 2
    assert commands[0].text == replay.AQUB_GENERAL_RELEASE_REPLAY_MESSAGES[0]
    assert commands[0].reply_to_message_id == 10
    assert commands[1].text == replay.AQUB_GENERAL_RELEASE_REPLAY_MESSAGES[1]
    assert commands[1].reply_to_message_id is None


@pytest.mark.anyio
async def test_send_replay_rejects_wrong_digest_before_database_access() -> None:
    session = FakeSession([])
    settings = SimpleNamespace(
        telegram_aqub_bot_token=SecretStr("synthetic-token"),
        gate_aqub_receipt_signing_key=SecretStr("s" * 32),
    )

    with pytest.raises(
        replay.AQUBGeneralReleaseReplayError,
        match="aqub_general_replay_digest_mismatch",
    ):
        await replay.send_aqub_general_release_replay(
            session=session,  # type: ignore[arg-type]
            settings=settings,  # type: ignore[arg-type]
            http_client=SimpleNamespace(),  # type: ignore[arg-type]
            installation_ref=replay.AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF,
            turn_ref=replay.AQUB_GENERAL_RELEASE_REPLAY_TURN_REF,
            expected_candidate_sha256="0" * 64,
            confirmed=True,
        )

    assert session.scalar_calls == 0


@pytest.mark.anyio
async def test_mcp_send_review_routes_the_exact_aqub_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "installation_ref": replay.AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF,
        "turn_ref": replay.AQUB_GENERAL_RELEASE_REPLAY_TURN_REF,
        "state": "sent",
        "replay": False,
        "provider_message_id_exposed": False,
        "participant_identities_exposed": False,
    }

    async def fake_send(**kwargs: object) -> dict[str, object]:
        assert kwargs["confirmed"] is True
        return expected

    monkeypatch.setattr(
        mcp_routes,
        "send_aqub_general_release_replay",
        fake_send,
    )
    session = FakeSession([])

    result = await mcp_routes._alpha_send_review_tool_result(
        request_id="replay",
        arguments={
            "installation_ref": replay.AQUB_GENERAL_RELEASE_REPLAY_INSTALLATION_REF,
            "turn_ref": replay.AQUB_GENERAL_RELEASE_REPLAY_TURN_REF,
            "expected_candidate_sha256": (
                replay.AQUB_GENERAL_RELEASE_REPLAY_CANDIDATE_SHA256
            ),
            "confirmed": True,
        },
        principal=SimpleNamespace(),
        session=session,  # type: ignore[arg-type]
        settings=SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert result["result"]["structuredContent"] == expected
    assert session.rollback_calls == 0
