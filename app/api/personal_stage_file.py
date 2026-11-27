"""Round-trip exactly one public test vector from a Work file attachment."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.api.personal_command_mcp import ERROR_SCHEMA
from app.personal_telegram.command_scope import CommandRuntimeError

logger = logging.getLogger("uvicorn.error")

FIXTURE = b"Gate MTProto RU S1 file roundtrip\n"
FIXTURE_PATH = "/s1/fixture.txt"
FILE_TOOL = "gate_personal_file_probe"
FILE_SCOPE = "gate:personal:jobs:read"
FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        key: {"type": "string"} for key in ("download_url", "file_id", "mime_type", "file_name")
    },
    "required": ["download_url", "file_id"],
    "additionalProperties": False,
}


def file_descriptor() -> dict[str, Any]:
    security = [{"type": "oauth2", "scopes": [FILE_SCOPE]}]
    return {
        "name": FILE_TOOL,
        "title": "Check the S1 fixture attachment",
        "description": (
            "Attach the unmodified public fixture from /s1/fixture.txt. "
            "Returns its size, SHA-256 and download link."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"file": FILE_SCHEMA},
            "required": ["file"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "bytes": {"type": "integer"},
                        "sha256": {"type": "string"},
                        "download_url": {"type": "string"},
                    },
                    "required": ["bytes", "sha256", "download_url"],
                    "additionalProperties": False,
                },
                ERROR_SCHEMA,
            ],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": True,
            "idempotentHint": True,
        },
        "securitySchemes": security,
        "_meta": {"securitySchemes": security, "openai/fileParams": ["file"]},
    }


async def check_attachment(arguments: Any, *, host: str, resource: str) -> dict[str, Any]:
    file = arguments.get("file") if isinstance(arguments, dict) else None
    if (
        not isinstance(file, dict)
        or set(arguments) != {"file"}
        or not {"download_url", "file_id"} <= file.keys()
        or not file.keys() <= FILE_SCHEMA["properties"].keys()
        or not all(isinstance(v, str) and 0 < len(v) <= 8192 for v in file.values())
    ):
        raise CommandRuntimeError("invalid_arguments")
    url = file["download_url"]
    if not host:
        try:
            observed = urlsplit(url).hostname or ""
        except ValueError:
            observed = ""
        if re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", observed):
            logger.info("gate696_s1_file_route host=%s", observed)
        raise CommandRuntimeError("work_file_host_unconfigured")
    try:
        parsed = urlsplit(url)
        if (
            not url.isascii()
            or any(ord(ch) < 33 or ord(ch) == 127 for ch in url)
            or "\\" in url
            or parsed.scheme != "https"
            or parsed.netloc != host
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise ValueError
    except ValueError:
        raise CommandRuntimeError("work_file_host_denied") from None
    # No redirects or HTTP client request logging of the signed URL.
    try:
        async with asyncio.timeout(8), httpx.AsyncHTTPTransport(trust_env=False) as transport:
            response = await transport.handle_async_request(
                httpx.Request("GET", url, headers={"Accept-Encoding": "identity"})
            )
            try:
                if (
                    response.status_code != 200
                    or response.headers.get("Content-Encoding", "identity") != "identity"
                ):
                    raise CommandRuntimeError("work_file_unavailable")
                size = response.headers.get("Content-Length")
                if size is not None and size != str(len(FIXTURE)):
                    raise CommandRuntimeError("fixture_mismatch")
                contents = bytearray()
                async for chunk in response.aiter_raw():
                    if len(contents) + len(chunk) > len(FIXTURE):
                        raise CommandRuntimeError("fixture_mismatch")
                    contents.extend(chunk)
            finally:
                await response.aclose()
    except (TimeoutError, httpx.HTTPError, OSError):
        raise CommandRuntimeError("work_file_unavailable") from None
    if contents != FIXTURE:
        raise CommandRuntimeError("fixture_mismatch")
    return {
        "bytes": len(contents),
        "sha256": hashlib.sha256(contents).hexdigest(),
        "download_url": resource.removesuffix("/mcp") + FIXTURE_PATH,
    }
