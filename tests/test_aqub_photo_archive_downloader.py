"""Bounded, fixed-origin Telegram photo downloader contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from app.application.aqub_photo_archive import (
    TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES,
    AQUBPhotoArchiveClaim,
    AQUBPhotoArchiveError,
    DownloadedAQUBPhoto,
)
from app.infrastructure import aqub_photo_downloader as adapter
from app.services.telegram import build_telegram_http_client


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes, delay: float = 0.0) -> None:
        self._chunks = chunks
        self._delay = delay

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk


def _claim(
    *,
    declared_size: int | None = None,
    attempt: int = 1,
) -> AQUBPhotoArchiveClaim:
    return AQUBPhotoArchiveClaim(
        archive_id=uuid4(),
        receipt_id=uuid4(),
        lease_token=uuid4(),
        lease_generation=attempt,
        attempt_count=attempt,
        provider_file_id="provider-file-id",
        source_payload_sha256="a" * 64,
        file_unique_digest="b" * 64,
        declared_size_bytes=declared_size,
    )


def _metadata(*, path: object = "photos/owner.jpg", size: object = None) -> bytes:
    result: dict[str, object] = {"file_path": path}
    if size is not None:
        result["file_size"] = size
    return json.dumps({"ok": True, "result": result}).encode()


@pytest.mark.anyio
async def test_download_uses_one_fixed_get_file_and_one_stream_with_magic_only() -> None:
    raw = b"\xff\xd8\xffnot-a-real-jpeg-but-magic-is-bounded"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                stream=_Chunks(_metadata(size=len(raw))),
                headers={"content-encoding": "identity"},
            )
        return httpx.Response(
            200,
            stream=_Chunks(raw[:4], raw[4:]),
            headers={
                "content-length": str(len(raw)),
                "content-type": "text/plain",
                "content-encoding": "identity",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        downloaded = await adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        ).download(claim=_claim(declared_size=len(raw)))

    assert downloaded.data == raw
    assert downloaded.sha256 == hashlib.sha256(raw).hexdigest()
    assert downloaded.media_type == "image/jpeg"
    assert len(requests) == 2
    assert requests[0].method == "POST"
    assert requests[0].url == "https://api.telegram.org/botbot-token/getFile"
    assert requests[1].method == "GET"
    assert requests[1].url == (
        "https://api.telegram.org/file/botbot-token/photos/owner.jpg"
    )
    assert all(request.headers["accept-encoding"] == "identity" for request in requests)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/absolute.jpg",
        "https://evil.invalid/x.jpg",
        "//evil.invalid/x.jpg",
        "photos\\owner.jpg",
        "photos/../owner.jpg",
        "photos/./owner.jpg",
        "photos//owner.jpg",
        "photos/owner.jpg?secret=1",
        "photos/owner.jpg#fragment",
        "photos/control\n.jpg",
    ],
)
async def test_unsafe_provider_path_stops_before_file_get(unsafe_path: str) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=_Chunks(_metadata(path=unsafe_path)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.code == "aqub_photo_file_path_invalid"
    assert len(requests) == 1


@pytest.mark.anyio
async def test_streamed_429_preserves_bounded_retry_after() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, stream=_Chunks(_metadata()))
        body = json.dumps({"parameters": {"retry_after": 17}}).encode()
        return httpx.Response(429, stream=_Chunks(body[:3], body[3:]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.code == "aqub_photo_provider_rate_limited"
    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds == 17
    assert len(requests) == 2


@pytest.mark.anyio
async def test_metadata_auth_status_precedes_unexpected_encoding() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            stream=_Chunks(b"{}"),
            headers={"content-encoding": "gzip"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.code == "aqub_photo_provider_auth_rejected"
    assert caught.value.retryable is False


@pytest.mark.anyio
async def test_metadata_body_cap_and_declared_cap_stop_without_file_bytes() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=_Chunks(b"{" + b"x" * 65_536))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as metadata_error:
            await downloader.download(claim=_claim())
        with pytest.raises(ValueError, match="aqub_photo_declared_size_invalid"):
            _claim(declared_size=TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES + 1)

    assert metadata_error.value.code == "aqub_photo_metadata_too_large"
    assert len(requests) == 1


@pytest.mark.anyio
async def test_unexpected_content_encoding_and_bad_magic_never_return_partial_data() -> None:
    raw = b"not-an-image"

    async def compressed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, stream=_Chunks(_metadata()))
        return httpx.Response(
            200,
            stream=_Chunks(raw),
            headers={"content-encoding": "gzip"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(compressed)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as encoding_error:
            await downloader.download(claim=_claim())
    assert encoding_error.value.code == "aqub_photo_content_encoding_rejected"

    async def uncompressed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, stream=_Chunks(_metadata(size=len(raw))))
        return httpx.Response(200, stream=_Chunks(raw))

    async with httpx.AsyncClient(transport=httpx.MockTransport(uncompressed)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as magic_error:
            await downloader.download(claim=_claim(declared_size=len(raw)))
    assert magic_error.value.code == "aqub_photo_media_type_unsupported"


@pytest.mark.anyio
async def test_attempt_has_hard_wall_clock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Chunks(_metadata(), delay=0.1))

    monkeypatch.setattr(adapter, "TELEGRAM_PHOTO_ATTEMPT_TIMEOUT_SECONDS", 0.01)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.code == "aqub_photo_attempt_timed_out"
    assert caught.value.retryable is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("oversize_at", "expected_code", "expected_calls"),
    [
        ("get_file", "aqub_photo_get_file_size_invalid", 1),
        ("content_length", "aqub_photo_content_length_invalid", 2),
        ("stream", "aqub_photo_download_limit_exceeded", 2),
    ],
)
async def test_each_provider_boundary_enforces_twenty_megabyte_cap(
    oversize_at: str,
    expected_code: str,
    expected_calls: int,
) -> None:
    requests: list[httpx.Request] = []
    megabyte = b"x" * 1_000_000

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getFile"):
            size = (
                TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES + 1
                if oversize_at == "get_file"
                else None
            )
            return httpx.Response(200, stream=_Chunks(_metadata(size=size)))
        if oversize_at == "content_length":
            return httpx.Response(
                200,
                stream=_Chunks(),
                headers={
                    "content-length": str(TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES + 1)
                },
            )
        return httpx.Response(
            200,
            stream=_Chunks(*(megabyte,) * 20, b"x"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.code == expected_code
    assert len(requests) == expected_calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure_at", "status", "expected_code", "retryable"),
    [
        ("get_file", 302, "aqub_photo_provider_redirect_rejected", False),
        ("get_file", 500, "aqub_photo_get_file_failed", True),
        ("file", 302, "aqub_photo_provider_redirect_rejected", False),
        ("file", 500, "aqub_photo_download_failed", True),
        ("file", 404, "aqub_photo_download_failed", True),
        ("file", 410, "aqub_photo_download_failed", True),
    ],
)
async def test_redirect_and_retryable_status_classification(
    failure_at: str,
    status: int,
    expected_code: str,
    retryable: bool,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getFile") and failure_at == "get_file":
            return httpx.Response(status, stream=_Chunks(b"{}"))
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, stream=_Chunks(_metadata()))
        return httpx.Response(status, stream=_Chunks(b"{}"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable
    assert len(requests) == (1 if failure_at == "get_file" else 2)


@pytest.mark.anyio
@pytest.mark.parametrize("failure_at", ["get_file", "file"])
async def test_network_failures_are_sanitized_and_retryable(
    failure_at: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "fake-token-must-never-escape"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getFile") and failure_at == "file":
            return httpx.Response(200, stream=_Chunks(_metadata()))
        raise httpx.ConnectError(f"synthetic leak {request.url}", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token=token,
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.retryable is True
    assert caught.value.code == (
        "aqub_photo_get_file_failed"
        if failure_at == "get_file"
        else "aqub_photo_download_failed"
    )
    assert token not in str(caught.value)
    assert token not in repr(caught.value)
    assert token not in repr(downloader)
    assert token not in caplog.text
    assert len(requests) == (1 if failure_at == "get_file" else 2)


@pytest.mark.anyio
async def test_success_info_logs_redact_token_file_id_and_file_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "success-token-must-never-escape"
    file_path = "photos/private-owner-success.jpg"
    raw = b"\xff\xd8\xffprivate"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                stream=_Chunks(_metadata(path=file_path, size=len(raw))),
            )
        return httpx.Response(200, stream=_Chunks(raw))

    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await adapter.TelegramAQUBPhotoDownloader(
            bot_token=token,
            http_client=client,
        ).download(claim=_claim(declared_size=len(raw)))

    assert "<telegram-bot-api-redacted>" in caplog.text
    assert token not in caplog.text
    assert "provider-file-id" not in caplog.text
    assert file_path not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("failure_at", ["get_file", "file"])
async def test_non_2xx_info_logs_redact_all_provider_identifiers(
    failure_at: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "failure-token-must-never-escape"
    file_path = "photos/private-owner-failure.jpg"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile") and failure_at == "get_file":
            return httpx.Response(401, stream=_Chunks(b"{}"))
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                stream=_Chunks(_metadata(path=file_path)),
            )
        return httpx.Response(500, stream=_Chunks(b"{}"))

    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token=token,
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError):
            await downloader.download(claim=_claim())

    assert "<telegram-bot-api-redacted>" in caplog.text
    assert token not in caplog.text
    assert "provider-file-id" not in caplog.text
    assert file_path not in caplog.text


@pytest.mark.anyio
async def test_retry_attempt_restarts_with_fresh_get_file_and_one_file_get() -> None:
    requests: list[httpx.Request] = []
    raw = b"RIFF\x05\x00\x00\x00WEBPbytes"
    file_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal file_calls
        requests.append(request)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, stream=_Chunks(_metadata(size=len(raw))))
        file_calls += 1
        if file_calls == 1:
            return httpx.Response(500, stream=_Chunks(b"{}"))
        return httpx.Response(200, stream=_Chunks(raw))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as first:
            await downloader.download(claim=_claim(attempt=1))
        second = await downloader.download(claim=_claim(attempt=2))

    assert first.value.code == "aqub_photo_download_failed"
    assert second.data == raw
    assert [(request.method, request.url.path.rsplit("/", 1)[-1]) for request in requests] == [
        ("POST", "getFile"),
        ("GET", "owner.jpg"),
        ("POST", "getFile"),
        ("GET", "owner.jpg"),
    ]


@pytest.mark.anyio
async def test_oversized_metadata_429_body_is_bounded_without_body_leak() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            stream=_Chunks(b"{" + b"x" * 65_536),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.code == "aqub_photo_provider_rate_limited"
    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds is None


@pytest.mark.anyio
async def test_oversized_file_429_body_is_bounded_without_body_leak() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, stream=_Chunks(_metadata()))
        return httpx.Response(429, stream=_Chunks(b"{" + b"x" * 8_192))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = adapter.TelegramAQUBPhotoDownloader(
            bot_token="bot-token",
            http_client=client,
        )
        with pytest.raises(AQUBPhotoArchiveError) as caught:
            await downloader.download(claim=_claim())

    assert caught.value.code == "aqub_photo_provider_rate_limited"
    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds is None


def test_contracts_reject_false_hash_file_id_and_invalid_retry_after() -> None:
    with pytest.raises(ValueError, match="aqub_photo_file_id_invalid"):
        AQUBPhotoArchiveClaim(
            archive_id=uuid4(),
            receipt_id=uuid4(),
            lease_token=uuid4(),
            lease_generation=1,
            attempt_count=1,
            provider_file_id=" file-id ",
            source_payload_sha256="a" * 64,
            file_unique_digest="b" * 64,
            declared_size_bytes=None,
        )
    with pytest.raises(ValueError, match="aqub_photo_retry_after_requires_retryable"):
        AQUBPhotoArchiveError("aqub_photo_test", retry_after_seconds=1)
    with pytest.raises(ValueError, match="aqub_photo_download_invalid"):
        DownloadedAQUBPhoto(
            data=b"\xff\xd8\xffbytes",
            sha256="0" * 64,
            size_bytes=8,
            media_type="image/jpeg",
        )
    with pytest.raises(ValueError, match="aqub_photo_download_invalid"):
        DownloadedAQUBPhoto(
            data=bytearray(b"\xff\xd8\xffbytes"),  # type: ignore[arg-type]
            sha256="0" * 64,
            size_bytes=8,
            media_type="image/jpeg",
        )
    with pytest.raises(ValueError, match="aqub_photo_lease_generation_invalid"):
        AQUBPhotoArchiveClaim(
            archive_id=uuid4(),
            receipt_id=uuid4(),
            lease_token=uuid4(),
            lease_generation=2,
            attempt_count=1,
            provider_file_id="file-id",
            source_payload_sha256="a" * 64,
            file_unique_digest="b" * 64,
            declared_size_bytes=None,
        )


@pytest.mark.anyio
async def test_process_owned_http_client_has_no_redirect_env_or_retry_ambiguity() -> None:
    client = build_telegram_http_client()
    try:
        assert client.follow_redirects is False
        assert client._trust_env is False
        transport = client._transport
        pool = transport._pool
        assert pool._retries == 0
    finally:
        await client.aclose()
