"""Validate one selected local document before staging it in a reply task."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

from app.application.reply_drafts import ReplyDraftError
from app.core.mcp_security import Principal

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BASE64_LENGTH = ((MAX_DOCUMENT_BYTES + 2) // 3) * 4


@dataclass(frozen=True, slots=True)
class PrepareReplyDocumentCommand:
    principal: Principal
    task_id: UUID
    filename: str
    media_type: str
    content_base64: str
    expected_sha256: str
    caption: str = ""
    follow_up: bool = False


@dataclass(frozen=True, slots=True)
class PreparedReplyDocument:
    draft_id: UUID
    task_id: UUID
    account_label: str
    recipient_chat_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    caption: str


class ReplyDocumentWriter(Protocol):
    async def save(
        self,
        command: PrepareReplyDocumentCommand,
        data: bytes,
    ) -> PreparedReplyDocument: ...


class PrepareReplyDocumentService:
    def __init__(self, writer: ReplyDocumentWriter) -> None:
        self._writer = writer

    async def execute(self, command: PrepareReplyDocumentCommand) -> PreparedReplyDocument:
        if "gate:drafts:write" not in command.principal.scopes:
            raise ReplyDraftError(
                code="insufficient_scope", message="Scope gate:drafts:write is required"
            )
        if not command.principal.access_scope.is_explicit:
            raise ReplyDraftError(
                code="invalid_access_scope",
                message="Explicit workspace, account and project grants are required",
            )
        filename = command.filename
        if (
            not filename
            or filename in {".", ".."}
            or PurePosixPath(filename).name != filename
            or "\\" in filename
            or any(ord(character) < 32 for character in filename)
            or len(filename.encode("utf-8")) > 255
        ):
            raise ReplyDraftError(code="invalid_document", message="Invalid filename")
        allowed = {".pdf": "application/pdf", ".zip": "application/zip"}
        if allowed.get(PurePosixPath(filename).suffix.lower()) != command.media_type:
            raise ReplyDraftError(code="invalid_document", message="Filename and MIME mismatch")
        if not isinstance(command.caption, str) or len(command.caption) > 1024:
            raise ReplyDraftError(
                code="invalid_document", message="Caption exceeds 1024 characters"
            )
        if (
            len(command.expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in command.expected_sha256)
        ):
            raise ReplyDraftError(code="invalid_document", message="Invalid SHA-256")
        if (
            not isinstance(command.content_base64, str)
            or not command.content_base64
            or len(command.content_base64) > MAX_DOCUMENT_BASE64_LENGTH
        ):
            raise ReplyDraftError(code="invalid_document", message="Document exceeds 8 MiB")
        try:
            data = base64.b64decode(command.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ReplyDraftError(
                code="invalid_document", message="Invalid document encoding"
            ) from exc
        if not 0 < len(data) <= MAX_DOCUMENT_BYTES:
            raise ReplyDraftError(code="invalid_document", message="Document size is invalid")
        is_pdf = command.media_type == "application/pdf" and b"%PDF-" in data[:1024]
        is_zip = command.media_type == "application/zip" and data.startswith(
            (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
        )
        if not (is_pdf or is_zip):
            raise ReplyDraftError(code="invalid_document", message="Document content type mismatch")
        digest = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(digest, command.expected_sha256):
            raise ReplyDraftError(code="document_changed", message="Document SHA-256 differs")
        return await self._writer.save(command, data)
