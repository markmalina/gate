import httpx
import pytest
from app.services.telegram import TelegramApiError, TelegramClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_telegram_client_parses_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            request = httpx.Request("POST", url)
            return httpx.Response(
                400,
                request=request,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: business connection not found",
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = TelegramClient("secret-token")

    with pytest.raises(TelegramApiError) as exc_info:
        await client.send_message(
            chat_id="123",
            text="hello",
            business_connection_id="connection",
        )

    assert exc_info.value.error_code == 400
    assert exc_info.value.http_status == 400
    assert exc_info.value.description == "Bad Request: business connection not found"
