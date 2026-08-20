"""Strict Telegram getFile adapter for one bounded AQUB owner photo."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.application.aqub_photo_archive import (
    TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES,
    AQUBPhotoArchiveClaim,
    AQUBPhotoArchiveError,
    DownloadedAQUBPhoto,
)

TELEGRAM_PUBLIC_API_ORIGIN = "https://api.telegram.org"
TELEGRAM_PHOTO_ATTEMPT_TIMEOUT_SECONDS = 90.0
_HTTPX_LOGGER = logging.getLogger("httpx")
_TELEGRAM_BOT_API_URL = re.compile(
    r"https://api\.telegram\.org/(?:file/)?bot[^/\s]+(?:/[^\s]*)?"
)


class _TelegramBotAPILogFilter(logging.Filter):
    """Remove token-bearing Telegram paths before HTTPX emits a log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_telegram_url(str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_log_argument(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: _redact_log_argument(value) for key, value in record.args.items()
            }
        return True


_TELEGRAM_BOT_API_LOG_FILTER = _TelegramBotAPILogFilter()


class TelegramAQUBPhotoDownloader:
    """Perform exactly one getFile and at most one streaming GET per claim."""

    def __init__(self, *, bot_token: str, http_client: httpx.AsyncClient) -> None:
        if not bot_token or bot_token != bot_token.strip():
            raise AQUBPhotoArchiveError("aqub_photo_bot_token_required")
        _install_telegram_bot_api_log_redaction()
        self._bot_token = bot_token
        self._http = http_client

    async def download(self, *, claim: AQUBPhotoArchiveClaim) -> DownloadedAQUBPhoto:
        try:
            async with asyncio.timeout(TELEGRAM_PHOTO_ATTEMPT_TIMEOUT_SECONDS):
                return await self._download_bounded(claim=claim)
        except TimeoutError:
            raise AQUBPhotoArchiveError(
                "aqub_photo_attempt_timed_out",
                retryable=True,
            ) from None

    async def _download_bounded(
        self,
        *,
        claim: AQUBPhotoArchiveClaim,
    ) -> DownloadedAQUBPhoto:
        declared_size = claim.declared_size_bytes
        if declared_size is not None and not 1 <= declared_size <= 20_000_000:
            raise AQUBPhotoArchiveError("aqub_photo_declared_size_invalid")

        metadata = await self._get_file(claim.provider_file_id)
        file_size = _optional_bounded_size(metadata.get("file_size"))
        file_path = _safe_provider_path(metadata.get("file_path"))
        safe_path = quote(file_path.as_posix(), safe="/")
        file_url = (
            f"{TELEGRAM_PUBLIC_API_ORIGIN}/file/bot{self._bot_token}/{safe_path}"
        )

        data = bytearray()
        digest = hashlib.sha256()
        content_length: int | None = None
        try:
            async with self._http.stream(
                "GET",
                file_url,
                timeout=30.0,
                follow_redirects=False,
                headers={"accept-encoding": "identity"},
            ) as response:
                await _raise_file_status(response)
                content_encoding = response.headers.get("content-encoding", "").lower()
                if content_encoding not in {"", "identity"}:
                    raise AQUBPhotoArchiveError("aqub_photo_content_encoding_rejected")
                content_length = _content_length(response.headers.get("content-length"))
                async for chunk in response.aiter_raw():
                    remaining = TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES + 1 - len(data)
                    accepted = chunk[:remaining]
                    data.extend(accepted)
                    digest.update(accepted)
                    if len(data) > TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES:
                        raise AQUBPhotoArchiveError("aqub_photo_download_limit_exceeded")
        except AQUBPhotoArchiveError:
            raise
        except httpx.HTTPError:
            raise AQUBPhotoArchiveError(
                "aqub_photo_download_failed",
                retryable=True,
            ) from None

        raw = bytes(data)
        if not raw:
            raise AQUBPhotoArchiveError("aqub_photo_download_empty")
        actual_size = len(raw)
        expected_sizes = tuple(
            size
            for size in (declared_size, file_size, content_length)
            if size is not None
        )
        if any(size != actual_size for size in expected_sizes):
            raise AQUBPhotoArchiveError(
                "aqub_photo_size_mismatch",
                retryable=True,
            )
        media_type = _sniff_media_type(raw)
        return DownloadedAQUBPhoto(
            data=raw,
            sha256=digest.hexdigest(),
            size_bytes=actual_size,
            media_type=media_type,
        )

    async def _get_file(self, file_id: str) -> dict[str, Any]:
        try:
            async with self._http.stream(
                "POST",
                f"{TELEGRAM_PUBLIC_API_ORIGIN}/bot{self._bot_token}/getFile",
                json={"file_id": file_id},
                timeout=15.0,
                follow_redirects=False,
                headers={"accept-encoding": "identity"},
            ) as response:
                body = await _bounded_response_body(response, limit=65_536)
        except httpx.HTTPError:
            raise AQUBPhotoArchiveError(
                "aqub_photo_get_file_failed",
                retryable=True,
            ) from None
        payload = _json_object(body)
        _raise_metadata_status(response, payload)
        if response.headers.get("content-encoding", "").lower() not in {"", "identity"}:
            raise AQUBPhotoArchiveError("aqub_photo_content_encoding_rejected")
        if body is None:
            raise AQUBPhotoArchiveError("aqub_photo_metadata_too_large")
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise AQUBPhotoArchiveError("aqub_photo_get_file_rejected")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise AQUBPhotoArchiveError("aqub_photo_get_file_invalid")
        return {str(key): value for key, value in result.items()}


def _install_telegram_bot_api_log_redaction() -> None:
    if _TELEGRAM_BOT_API_LOG_FILTER not in _HTTPX_LOGGER.filters:
        _HTTPX_LOGGER.addFilter(_TELEGRAM_BOT_API_LOG_FILTER)


def _redact_log_argument(value: Any) -> Any:
    if isinstance(value, (str, httpx.URL)):
        return _redact_telegram_url(str(value))
    return value


def _redact_telegram_url(value: str) -> str:
    return _TELEGRAM_BOT_API_URL.sub(
        f"{TELEGRAM_PUBLIC_API_ORIGIN}/<telegram-bot-api-redacted>",
        value,
    )


def _safe_provider_path(value: Any) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AQUBPhotoArchiveError("aqub_photo_file_path_invalid")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise AQUBPhotoArchiveError("aqub_photo_file_path_invalid")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise AQUBPhotoArchiveError("aqub_photo_file_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise AQUBPhotoArchiveError("aqub_photo_file_path_invalid")
    return path


def _optional_bounded_size(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES:
        raise AQUBPhotoArchiveError("aqub_photo_get_file_size_invalid")
    return value


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.isdecimal():
        raise AQUBPhotoArchiveError("aqub_photo_content_length_invalid")
    size = int(value)
    if not 1 <= size <= TELEGRAM_PHOTO_DOWNLOAD_LIMIT_BYTES:
        raise AQUBPhotoArchiveError("aqub_photo_content_length_invalid")
    return size


def _sniff_media_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise AQUBPhotoArchiveError("aqub_photo_media_type_unsupported")


def _raise_metadata_status(response: httpx.Response, payload: Any) -> None:
    status = response.status_code
    if status == 200:
        return
    if status == 429:
        raise AQUBPhotoArchiveError(
            "aqub_photo_provider_rate_limited",
            retryable=True,
            retry_after_seconds=_retry_after(payload),
        )
    if status >= 500:
        raise AQUBPhotoArchiveError("aqub_photo_get_file_failed", retryable=True)
    if 300 <= status < 400:
        raise AQUBPhotoArchiveError("aqub_photo_provider_redirect_rejected")
    if status in {401, 403}:
        raise AQUBPhotoArchiveError("aqub_photo_provider_auth_rejected")
    raise AQUBPhotoArchiveError("aqub_photo_get_file_rejected")


async def _raise_file_status(response: httpx.Response) -> None:
    status = response.status_code
    if status == 200:
        return
    if status == 429:
        payload = _json_object(
            await _bounded_response_body(response, limit=8_192)
        )
        raise AQUBPhotoArchiveError(
            "aqub_photo_provider_rate_limited",
            retryable=True,
            retry_after_seconds=_retry_after(payload),
        )
    if status >= 500 or status in {404, 410}:
        raise AQUBPhotoArchiveError("aqub_photo_download_failed", retryable=True)
    if 300 <= status < 400:
        raise AQUBPhotoArchiveError("aqub_photo_provider_redirect_rejected")
    if status in {401, 403}:
        raise AQUBPhotoArchiveError("aqub_photo_provider_auth_rejected")
    raise AQUBPhotoArchiveError("aqub_photo_download_rejected")


def _retry_after(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("retry_after")
    return value if type(value) is int and 1 <= value <= 86_400 else None


async def _bounded_response_body(
    response: httpx.Response,
    *,
    limit: int,
) -> bytes | None:
    data = bytearray()
    async for chunk in response.aiter_raw():
        remaining = limit + 1 - len(data)
        data.extend(chunk[:remaining])
        if len(data) > limit:
            return None
    return bytes(data)


def _json_object(body: bytes | None) -> Any:
    if body is None:
        return None
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
