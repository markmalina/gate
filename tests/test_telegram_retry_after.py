import asyncio

import httpx
import pytest
from app.services.telegram import TelegramApiError, TelegramClient


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ({"retry_after": 0}, 0),
        ({"retry_after": 120}, 120),
        ({"retry_after": 10**100}, 10**100),
        ({"retry_after": True}, None),
        ({"retry_after": False}, None),
        ({"retry_after": -1}, None),
        ({"retry_after": "120"}, None),
        ({"retry_after": 1.5}, None),
        ({"retry_after": None}, None),
        ({}, None),
        (None, None),
        ([], None),
        ("untrusted", None),
    ],
)
@pytest.mark.parametrize("method", ["text", "document"])
def test_retry_after_is_structured_and_transport_never_retries(
    parameters, expected, method
) -> None:
    async def scenario() -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                429,
                request=request,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": parameters,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client = TelegramClient("synthetic", http_client=transport)
            with pytest.raises(TelegramApiError) as error:
                if method == "text":
                    await client.send_plain_message(chat_id="synthetic", text="test")
                else:
                    await client.send_business_document(
                        chat_id="synthetic",
                        business_connection_id="synthetic",
                        filename="test.pdf",
                        media_type="application/pdf",
                        data=b"test",
                    )
            assert error.value.retry_after_seconds == expected
            assert error.value.error_code == 429
            assert error.value.http_status == 429
            assert str(error.value) == "Too Many Requests"
            assert calls == 1

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_existing_error_constructor_remains_compatible() -> None:
    error = TelegramApiError(error_code=400, description="synthetic")
    assert error.retry_after_seconds is None


@pytest.mark.parametrize("expired", [True, False])
def test_sequence_start_window_is_rechecked_at_http_adapter_boundary(monkeypatch, expired):
    from app.services import telegram_delivery_gate as gate

    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        monkeypatch.setattr(gate.time, "monotonic", lambda: 100.2 if expired else 100.05)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client = TelegramClient("123:synthetic", http_client=transport)
            with gate.reserved_delivery("123:synthetic", admitted_at=100):
                if expired:
                    with pytest.raises(gate.TelegramDispatchStartExpired):
                        await client.send_plain_message(chat_id="synthetic", text="test")
                else:
                    await client.send_plain_message(chat_id="synthetic", text="test")
        assert len(calls) == (0 if expired else 1)

    asyncio.run(scenario())
