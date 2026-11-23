import asyncio
import json

import httpx
import pytest
from app.services.telegram import (
    TelegramApiError,
    TelegramClient,
    build_telegram_http_client,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_telegram_client_parses_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            request=request,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: business connection not found",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)

        with pytest.raises(TelegramApiError) as exc_info:
            await client.send_plain_message(
                chat_id="123",
                text="hello",
                business_connection_id="connection",
            )

    assert exc_info.value.error_code == 400
    assert exc_info.value.http_status == 400
    assert exc_info.value.description == "Bad Request: business connection not found"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text",
    [
        "ordinary literal text",
        "Keep <code>this</code> literal and do not parse it.",
        "Keep <pre>this block</pre> literal and do not parse it.",
        "Draft created: 01234567-89ab-cdef-0123-456789abcdef",
        "Edit draft:\nhttps://gate.example/admin/drafts/example",
    ],
)
async def test_send_plain_message_preserves_exact_literal_payload(text: str) -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "result": {"message_id": 123}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)
        await client.send_plain_message(
            chat_id="123",
            text=text,
            business_connection_id="business-connection",
        )

    assert payloads == [
        {
            "chat_id": "123",
            "text": text,
            "business_connection_id": "business-connection",
        }
    ]


@pytest.mark.anyio
async def test_send_plain_message_quotes_exact_source_message() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "result": {"message_id": 124}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)
        await client.send_plain_message(
            chat_id="123",
            text="quoted answer",
            reply_to_message_id=501,
        )

    assert payloads == [
        {
            "chat_id": "123",
            "text": "quoted answer",
            "reply_parameters": {"message_id": 501},
        }
    ]


@pytest.mark.anyio
async def test_telegram_client_reuses_injected_transport_without_owning_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json={"ok": True, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)
        await client.send_message(chat_id="123", text="first")
        await client.send_message(chat_id="123", text="second")

        assert len(requests) == 2
        assert not http_client.is_closed

    assert http_client.is_closed


def test_telegram_http_client_has_explicit_resource_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = build_telegram_http_client()

    assert isinstance(client, FakeAsyncClient)
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 5.0
    assert timeout.read == 10.0
    assert timeout.write == 5.0
    assert timeout.pool == 2.0
    limits = captured["limits"]
    assert isinstance(limits, httpx.Limits)
    assert limits.max_connections == 10
    assert limits.max_keepalive_connections == 5
    assert limits.keepalive_expiry == 30.0
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False


@pytest.mark.anyio
async def test_telegram_client_propagates_cancellation_without_retrying() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)

        with pytest.raises(asyncio.CancelledError):
            await client.send_message(chat_id="123", text="cancel")

    assert calls == 1


@pytest.mark.anyio
async def test_telegram_client_does_not_retry_unknown_transport_outcome() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("delivery outcome unknown", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)

        with pytest.raises(httpx.ReadTimeout):
            await client.send_message(chat_id="123", text="timeout")

    assert calls == 1


@pytest.mark.anyio
async def test_send_business_document_uses_multipart_and_exact_business_binding() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "ok": True,
                "result": {
                    "message_id": 42,
                    "document": {
                        "file_id": "opaque-file-id",
                        "file_unique_id": "opaque-file-unique-id",
                        "file_name": "review.pdf",
                        "mime_type": "application/pdf",
                        "file_size": 12,
                    },
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)
        result = await client.send_business_document(
            chat_id="123",
            business_connection_id="business-connection",
            filename="review.pdf",
            media_type="application/pdf",
            data=b"%PDF-sample\n",
            caption="Ready",
        )

    assert result["result"]["message_id"] == 42
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path.endswith("/sendDocument")
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.content
    assert b'name="chat_id"\r\n\r\n123' in body
    assert b'name="business_connection_id"\r\n\r\nbusiness-connection' in body
    assert b'name="document"; filename="review.pdf"' in body
    assert b"Content-Type: application/pdf" in body
    assert b"%PDF-sample\n" in body


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "media_type", "data", "caption"),
    [
        ("../secret.pdf", "application/pdf", b"file", ""),
        ("secret\\file.pdf", "application/pdf", b"file", ""),
        ("review.pdf", "application/zip", b"file", ""),
        ("review.zip", "application/zip", b"", ""),
        ("review.zip", "application/zip", b"x" * (8 * 1024 * 1024 + 1), ""),
        ("review.pdf", "application/pdf", b"file", "x" * 1025),
    ],
)
async def test_send_business_document_rejects_invalid_file_before_provider(
    filename: str, media_type: str, data: bytes, caption: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)
        with pytest.raises(ValueError):
            await client.send_business_document(
                chat_id="123",
                business_connection_id="business-connection",
                filename=filename,
                media_type=media_type,
                data=data,
                caption=caption,
            )

    assert requests == []


@pytest.mark.anyio
async def test_send_business_document_does_not_retry_unknown_outcome() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("unknown", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient("secret-token", http_client=http_client)
        with pytest.raises(httpx.ReadTimeout):
            await client.send_business_document(
                chat_id="123",
                business_connection_id="business-connection",
                filename="bundle.zip",
                media_type="application/zip",
                data=b"archive",
            )

    assert calls == 1
