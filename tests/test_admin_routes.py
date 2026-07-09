"""Read-only admin endpoint tests."""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api.deps import get_db_session
from app.core.config import get_settings
from app.main import app
from app.models.domain import AuditLog, GateEvent, Message, MessageDirection
from httpx import ASGITransport, AsyncClient

AdminRow = GateEvent | AuditLog | Message


class FakeScalarRows:
    """Minimal scalar row collection fake."""

    def __init__(self, rows: list[AdminRow]) -> None:
        self._rows = rows

    def all(self) -> list[AdminRow]:
        return self._rows


class FakeResult:
    """Minimal SQLAlchemy result fake."""

    def __init__(
        self,
        rows: list[AdminRow] | None = None,
        scalar: Any | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self) -> FakeScalarRows:
        return FakeScalarRows(self._rows)

    def scalar_one(self) -> Any:
        return self._scalar


class FakeAdminSession:
    """Minimal async session fake for read-only admin endpoints."""

    def __init__(self, rows: list[AdminRow]) -> None:
        self.rows = rows
        self.scalar_results: list[Any] = []

    async def execute(self, statement: object) -> FakeResult:
        del statement
        if self.scalar_results:
            return FakeResult(scalar=self.scalar_results.pop(0))
        return FakeResult(rows=self.rows)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def admin_context(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAdminSession]:
    monkeypatch.setenv("ADMIN_BEARER_TOKEN", "admin-token")
    get_settings.cache_clear()

    session = FakeAdminSession([])

    async def override_session() -> AsyncIterator[FakeAdminSession]:
        yield session

    app.dependency_overrides[get_db_session] = override_session

    yield session

    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_admin_events_requires_configured_token(
    admin_context: FakeAdminSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADMIN_BEARER_TOKEN", raising=False)
    get_settings.cache_clear()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/admin/events/recent")

    assert response.status_code == 503
    assert admin_context.rows == []


@pytest.mark.anyio
async def test_admin_events_rejects_invalid_bearer_token(
    admin_context: FakeAdminSession,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/events/recent",
            headers={"Authorization": "Bearer wrong"},
        )

    assert response.status_code == 403
    assert admin_context.rows == []


@pytest.mark.anyio
async def test_admin_events_returns_recent_events_without_raw_payload(
    admin_context: FakeAdminSession,
) -> None:
    event_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    admin_context.rows.append(
        GateEvent(
            id=event_id,
            bot_id=bot_id,
            chat_id="222",
            sender_id="111",
            event_type="message",
            text="ping",
            payload={"telegram_bot_token": "must-not-leak"},
            telegram_update_id=100,
            telegram_message_id=10,
            received_at=datetime(2026, 7, 9, 6, 0, tzinfo=UTC),
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/events/recent",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "events": [
            {
                "id": str(event_id),
                "bot_id": str(bot_id),
                "account_id": None,
                "project_id": None,
                "chat_id": "222",
                "sender_id": "111",
                "event_type": "message",
                "text": "ping",
                "telegram_update_id": 100,
                "telegram_message_id": 10,
                "business_connection_id": None,
                "received_at": "2026-07-09T06:00:00+00:00",
            }
        ]
    }
    assert "payload" not in body["events"][0]
    assert "telegram_bot_token" not in str(body)


@pytest.mark.anyio
async def test_admin_audit_returns_recent_audit_logs(
    admin_context: FakeAdminSession,
) -> None:
    audit_id = uuid.uuid4()
    gate_event_id = uuid.uuid4()
    admin_context.rows.append(
        AuditLog(
            id=audit_id,
            actor="gate",
            action="auto_safe_reply_sent",
            gate_event_id=gate_event_id,
            metadata_={"reply": "Gate online", "rule": "ping"},
            created_at=datetime(2026, 7, 9, 6, 1, tzinfo=UTC),
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/audit/recent",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "audit_logs": [
            {
                "id": str(audit_id),
                "actor": "gate",
                "action": "auto_safe_reply_sent",
                "bot_id": None,
                "account_id": None,
                "project_id": None,
                "gate_event_id": str(gate_event_id),
                "metadata": {"reply": "Gate online", "rule": "ping"},
                "created_at": "2026-07-09T06:01:00+00:00",
            }
        ]
    }


@pytest.mark.anyio
async def test_admin_status_returns_operational_snapshot(
    admin_context: FakeAdminSession,
) -> None:
    admin_context.scalar_results.extend(
        [
            2,
            datetime(2026, 7, 9, 6, 2, tzinfo=UTC),
            4,
            datetime(2026, 7, 9, 6, 3, tzinfo=UTC),
            6,
            datetime(2026, 7, 9, 6, 4, tzinfo=UTC),
        ]
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/status",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["service"]["status"] == "ok"
    assert body["service"]["name"] == "gate"
    assert isinstance(body["service"]["environment"], str)
    assert isinstance(body["service"]["default_mode"], str)
    assert isinstance(body["deployment"]["service"], str)
    assert isinstance(body["deployment"]["commit_sha"], str)
    assert body["deployment"]["bot_username"] == "gate_m_bot"
    assert body["events"] == {
        "count": 2,
        "last_received_at": "2026-07-09T06:02:00+00:00",
    }
    assert body["audit_logs"] == {
        "count": 4,
        "last_created_at": "2026-07-09T06:03:00+00:00",
    }
    assert body["messages"] == {
        "count": 6,
        "last_created_at": "2026-07-09T06:04:00+00:00",
    }
    assert "payload" not in str(body)
    assert "telegram_bot_token" not in str(body)
    assert "ADMIN_BEARER_TOKEN" not in str(body)


@pytest.mark.anyio
async def test_admin_status_message_shape_uses_plain_counts(
    admin_context: FakeAdminSession,
) -> None:
    bot_id = uuid.uuid4()
    admin_context.rows.append(
        Message(
            bot_id=bot_id,
            direction=MessageDirection.INBOUND.value,
            source="telegram",
            event_type="message",
            chat_id="222",
            sender_id="111",
            text="hello",
            payload={},
        )
    )

    admin_context.scalar_results.extend([0, None, 0, None, 1, None])

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/status",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 200
    assert response.json()["messages"] == {"count": 1, "last_created_at": None}
