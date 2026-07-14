"""Safety and contract tests for the disabled Oko attribution boundary."""

import asyncio

import httpx
import pytest

from app.application.oko_attribution import (
    OkoAttributionDisposition,
    OkoAttributionService,
    OkoTelegramStart,
    parse_oko_telegram_start,
)
from app.infrastructure.oko_attribution import (
    HttpOkoAttributionClient,
    OkoAttributionError,
)

TOKEN = "tracking_token_1234567890"


def _update(text: str) -> dict:
    return {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "text": text,
            "from": {"id": 123456789, "username": "person"},
        },
    }


def test_parser_accepts_oko_start_and_bot_qualified_command() -> None:
    plain = parse_oko_telegram_start(_update(f"/start oko21_{TOKEN}"))
    qualified = parse_oko_telegram_start(
        _update(f"/start@gate_m_bot oko21_{TOKEN}")
    )

    assert plain == OkoTelegramStart(TOKEN, "123456789", "person")
    assert qualified == plain


@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "/start other_campaign",
        "/start oko21_short",
        f"hello /start oko21_{TOKEN}",
        f"/start oko21_{TOKEN} extra",
    ],
)
def test_parser_ignores_unrelated_or_malformed_text(text: str) -> None:
    assert parse_oko_telegram_start(_update(text)) is None


class ExplodingPort:
    async def record_telegram_start(self, start: OkoTelegramStart) -> None:
        raise AssertionError("disabled attribution must not perform network I/O")


def test_service_fails_closed_when_disabled() -> None:
    service = OkoAttributionService(enabled=False, port=ExplodingPort())

    result = asyncio.run(service.handle(_update(f"/start oko21_{TOKEN}")))

    assert result == OkoAttributionDisposition.DISABLED


def test_http_adapter_matches_oko_control_contract() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["json"] = request.read().decode()
        return httpx.Response(202, json={"event_id": "event", "lead_id": "lead"})

    async def run() -> OkoAttributionDisposition:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            port = HttpOkoAttributionClient(
                base_url="https://oko.example/control",
                bearer_secret="gate-secret",
                client=client,
            )
            return await OkoAttributionService(enabled=True, port=port).handle(
                _update(f"/start oko21_{TOKEN}")
            )

    assert asyncio.run(run()) == OkoAttributionDisposition.RECORDED
    assert captured["url"] == "https://oko.example/control/v1/gate/telegram-start"
    assert captured["authorization"] == "Bearer gate-secret"
    assert TOKEN in str(captured["json"])


def test_http_error_is_redacted() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"private {TOKEN}")

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            port = HttpOkoAttributionClient(
                base_url="https://oko.example",
                bearer_secret="gate-secret",
                client=client,
            )
            await port.record_telegram_start(
                OkoTelegramStart(TOKEN, "123456789", "person")
            )

    with pytest.raises(OkoAttributionError) as exc:
        asyncio.run(run())

    assert str(exc.value) == "oko_attribution_request_failed"
    assert TOKEN not in str(exc.value)
