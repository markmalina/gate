"""Regression tests for durable owner-only Alpha delivery receipts."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.infrastructure.alpha_owner_test_runtime import (
    AlphaOwnerTestRuntimeError,
    observe_owner_test_presence,
    send_owner_test_review,
    verify_owner_test_review_replay,
)
from app.models.alpha import AgentSendAttemptModel, DeliveryGrantModel


class _Result:
    def __init__(self, row: Any | None) -> None:
        self.row = row

    def one_or_none(self) -> Any | None:
        return self.row


class _Ledger:
    def __init__(self) -> None:
        self.grant: DeliveryGrantModel | None = None
        self.attempt: AgentSendAttemptModel | None = None


class _DeliverySession:
    def __init__(self, *, ledger: _Ledger, fixtures: SimpleNamespace) -> None:
        self.ledger = ledger
        self.fixtures = fixtures
        self.execute_count = 0
        self.pending: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement: object) -> _Result:
        self.execute_count += 1
        if self.execute_count == 1:
            return _Result(
                (
                    self.fixtures.installation,
                    self.fixtures.dialog,
                    self.fixtures.turn,
                    self.fixtures.trigger,
                )
            )
        if self.execute_count == 2:
            if self.ledger.grant is None or self.ledger.attempt is None:
                return _Result(None)
            return _Result((self.ledger.grant, self.ledger.attempt))
        if self.execute_count == 3:
            return _Result(
                (
                    self.fixtures.installation,
                    self.fixtures.dialog,
                    self.fixtures.connector,
                )
            )
        raise AssertionError("unexpected database query")

    def add(self, value: Any) -> None:
        self.pending.append(value)

    async def flush(self) -> None:
        for value in self.pending:
            if getattr(value, "id", None) is None:
                value.id = uuid4()

    async def commit(self) -> None:
        self.commits += 1
        for value in self.pending:
            if isinstance(value, DeliveryGrantModel):
                self.ledger.grant = value
            elif isinstance(value, AgentSendAttemptModel):
                self.ledger.attempt = value
        self.pending.clear()

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.pending.clear()


class _ReplaySession:
    def __init__(self, *, ledger: _Ledger, fixtures: SimpleNamespace) -> None:
        self.ledger = ledger
        self.fixtures = fixtures
        self.execute_count = 0
        self.commits = 0

    async def execute(self, _statement: object) -> _Result:
        self.execute_count += 1
        if self.execute_count == 1:
            return _Result((self.fixtures.installation, self.fixtures.turn))
        if self.execute_count == 2:
            if self.ledger.grant is None or self.ledger.attempt is None:
                return _Result(None)
            return _Result((self.ledger.grant, self.ledger.attempt))
        raise AssertionError("unexpected database query")


class _PresenceSession:
    def __init__(self, *, fixtures: SimpleNamespace) -> None:
        self.fixtures = fixtures
        self.commits = 0

    async def execute(self, _statement: object) -> _Result:
        return _Result(
            (
                self.fixtures.installation,
                self.fixtures.dialog,
                self.fixtures.connector,
            )
        )

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class _HttpClient:
    def __init__(self, *, ledger: _Ledger, member_count: int = 2) -> None:
        self.ledger = ledger
        self.member_count = member_count
        self.send_count = 0
        self.endpoints: list[str] = []

    async def post(
        self,
        endpoint: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> _Response:
        self.endpoints.append(endpoint)
        assert json["chat_id"] == "-100"
        assert timeout == 10.0
        if endpoint.endswith("/getChatMemberCount"):
            return _Response({"ok": True, "result": self.member_count})
        assert endpoint.endswith("/sendMessage")
        assert self.ledger.attempt is not None
        assert self.ledger.attempt.state == "unknown"
        assert (
            self.ledger.attempt.error_code
            == "alpha_owner_test_provider_outcome_pending"
        )
        self.send_count += 1
        return _Response(
            {"ok": True, "result": {"message_id": 1000 + self.send_count}}
        )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        telegram_alpha_bot_token=SimpleNamespace(
            get_secret_value=lambda: "test-token"
        )
    )


def _become_settings(
    *,
    installation_key: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        telegram_become_bot_token=SimpleNamespace(
            get_secret_value=lambda: "become-test-token"
        ),
        gate_become_connector_enabled=True,
        gate_become_canary_enabled=True,
        gate_become_privacy_mode_confirmed=True,
        gate_become_group_capacity=1,
        gate_become_installation_key=installation_key,
    )


def _fixtures() -> SimpleNamespace:
    candidate = "Альфа на связи."
    digest = sha256(candidate.encode()).hexdigest()
    workspace_id = uuid4()
    installation_id = uuid4()
    trigger_id = uuid4()
    turn_id = UUID("df3f1ba8-a0a6-426a-985d-285f8cca7ad4")
    return SimpleNamespace(
        digest=digest,
        installation=SimpleNamespace(
            id=installation_id,
            workspace_id=workspace_id,
            installation_key="installation-test",
            dialog_label="Alpha Group",
            state="active",
            updated_at=None,
        ),
        dialog=SimpleNamespace(provider_dialog_id="-100"),
        connector=SimpleNamespace(connector_key="telegram_alpha_group"),
        turn=SimpleNamespace(
            id=turn_id,
            trigger_message_id=trigger_id,
            status="proposed",
            result_kind="model_proposal",
            result_code=None,
            candidate_text=candidate,
            candidate_sha256=digest,
            context_message_ids=(),
            created_at=datetime(2026, 7, 22, tzinfo=UTC),
        ),
        trigger=SimpleNamespace(
            text_content="/alpha Ответь одним сообщением: Альфа на связи.",
            input_origin="telegram_user",
        ),
    )


@pytest.mark.anyio
async def test_exact_delivery_replays_across_independent_sessions() -> None:
    fixtures = _fixtures()
    ledger = _Ledger()
    http_client = _HttpClient(ledger=ledger)

    first = await send_owner_test_review(
        session=_DeliverySession(ledger=ledger, fixtures=fixtures),  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        http_client=http_client,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        installation_ref="installation-test",
        turn_ref=str(fixtures.turn.id),
        expected_candidate_sha256=fixtures.digest,
        confirmed=True,
    )

    second_session = _DeliverySession(ledger=ledger, fixtures=fixtures)
    second = await send_owner_test_review(
        session=second_session,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        http_client=http_client,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        installation_ref="installation-test",
        turn_ref=str(fixtures.turn.id),
        expected_candidate_sha256=fixtures.digest,
        confirmed=True,
    )

    assert first["state"] == "sent"
    assert first["replay"] is False
    assert second["state"] == "sent"
    assert second["replay"] is True
    assert http_client.send_count == 1
    assert second_session.commits == 0
    assert ledger.attempt is not None
    assert ledger.attempt.error_code is None


@pytest.mark.anyio
async def test_read_only_replay_proof_works_while_installation_is_paused() -> None:
    fixtures = _fixtures()
    ledger = _Ledger()
    http_client = _HttpClient(ledger=ledger)
    first = await send_owner_test_review(
        session=_DeliverySession(ledger=ledger, fixtures=fixtures),  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        http_client=http_client,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        installation_ref="installation-test",
        turn_ref=str(fixtures.turn.id),
        expected_candidate_sha256=fixtures.digest,
        confirmed=True,
    )
    fixtures.installation.state = "paused"
    proof_session = _ReplaySession(ledger=ledger, fixtures=fixtures)

    proof = await verify_owner_test_review_replay(
        session=proof_session,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        installation_ref="installation-test",
        turn_ref=str(fixtures.turn.id),
        expected_candidate_sha256=fixtures.digest,
    )

    assert first["state"] == "sent"
    assert proof["state"] == "sent"
    assert proof["replay"] is True
    assert http_client.send_count == 1
    assert proof_session.execute_count == 2
    assert proof_session.commits == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "installation_state",
    ("discovered", "pending_approval", "paused", "revoked"),
)
async def test_non_active_installation_blocks_before_provider(
    installation_state: str,
) -> None:
    fixtures = _fixtures()
    fixtures.installation.state = installation_state
    ledger = _Ledger()
    session = _DeliverySession(ledger=ledger, fixtures=fixtures)
    http_client = _HttpClient(ledger=ledger)

    with pytest.raises(
        AlphaOwnerTestRuntimeError,
        match="alpha_owner_test_installation_not_active",
    ):
        await send_owner_test_review(
            session=session,  # type: ignore[arg-type]
            settings=_settings(),  # type: ignore[arg-type]
            http_client=http_client,  # type: ignore[arg-type]
            workspace_key="mark-personal",
            installation_ref="installation-test",
            turn_ref=str(fixtures.turn.id),
            expected_candidate_sha256=fixtures.digest,
            confirmed=True,
        )

    assert session.execute_count == 1
    assert session.commits == 0
    assert ledger.grant is None
    assert ledger.attempt is None
    assert http_client.send_count == 0


@pytest.mark.anyio
async def test_become_presence_uses_only_isolated_token_while_paused() -> None:
    fixtures = _fixtures()
    fixtures.installation.dialog_label = "BECOME — Лёша · пилот"
    fixtures.installation.state = "paused"
    fixtures.connector.connector_key = "telegram_become_group"
    ledger = _Ledger()
    session = _PresenceSession(fixtures=fixtures)
    http_client = _HttpClient(ledger=ledger, member_count=2)

    presence = await observe_owner_test_presence(
        session=session,  # type: ignore[arg-type]
        settings=_become_settings(),  # type: ignore[arg-type]
        http_client=http_client,  # type: ignore[arg-type]
        workspace_key="mark-personal",
        installation_ref="installation-test",
    )

    assert presence.state.value == "owner_only"
    assert fixtures.installation.state == "paused"
    assert session.commits == 0
    assert len(http_client.endpoints) == 1
    assert "/botbecome-test-token/getChatMemberCount" in http_client.endpoints[0]
    assert "test-token" not in http_client.endpoints[0].replace(
        "become-test-token",
        "",
    )


@pytest.mark.anyio
async def test_become_presence_rejects_a_different_configured_pin() -> None:
    fixtures = _fixtures()
    fixtures.installation.dialog_label = "BECOME — Лёша · пилот"
    fixtures.installation.state = "paused"
    fixtures.connector.connector_key = "telegram_become_group"
    ledger = _Ledger()
    http_client = _HttpClient(ledger=ledger)

    with pytest.raises(
        AlphaOwnerTestRuntimeError,
        match="become_owner_test_installation_not_pinned",
    ):
        await observe_owner_test_presence(
            session=_PresenceSession(fixtures=fixtures),  # type: ignore[arg-type]
            settings=_become_settings(
                installation_key="installation-other",
            ),  # type: ignore[arg-type]
            http_client=http_client,  # type: ignore[arg-type]
            workspace_key="mark-personal",
            installation_ref="installation-test",
        )

    assert http_client.endpoints == []


@pytest.mark.anyio
async def test_non_owner_presence_persists_pause() -> None:
    fixtures = _fixtures()
    ledger = _Ledger()
    session = _PresenceSession(fixtures=fixtures)
    presence = await observe_owner_test_presence(
        session=session,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
        http_client=_HttpClient(  # type: ignore[arg-type]
            ledger=ledger,
            member_count=3,
        ),
        workspace_key="mark-personal",
        installation_ref="installation-test",
    )

    assert presence.state.value == "third_party_present"
    assert fixtures.installation.state == "paused"
    assert session.commits == 1
