"""Provider-neutral contracts for the Instagram transport."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

MAX_CAPTION_LENGTH = 2200
ALLOWED_VIDEO_MIME_TYPES = frozenset({"video/mp4", "video/quicktime"})


class InstagramContractError(ValueError):
    """Safe validation error suitable for an MCP response."""


class PublishState(StrEnum):
    PREPARED = "prepared"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InstagramFile:
    """A ChatGPT file parameter; the download URL is temporary and never logged."""

    download_url: str
    file_id: str
    mime_type: str
    file_name: str

    @classmethod
    def from_mapping(cls, value: Any) -> InstagramFile:
        if not isinstance(value, dict):
            raise InstagramContractError("video must be a ChatGPT file")
        download_url = value.get("download_url")
        file_id = value.get("file_id")
        mime_type = value.get("mime_type")
        file_name = value.get("file_name")
        fields = (download_url, file_id, mime_type, file_name)
        if not all(isinstance(item, str) and item.strip() for item in fields):
            raise InstagramContractError("video file metadata is incomplete")
        assert isinstance(download_url, str)
        assert isinstance(file_id, str)
        assert isinstance(mime_type, str)
        assert isinstance(file_name, str)
        if mime_type not in ALLOWED_VIDEO_MIME_TYPES:
            raise InstagramContractError("video must be MP4 or QuickTime")
        if not download_url.startswith("https://"):
            raise InstagramContractError("video download URL must use HTTPS")
        return cls(
            download_url=download_url,
            file_id=file_id,
            mime_type=mime_type,
            file_name=file_name,
        )

    def public_summary(self) -> dict[str, str]:
        return {
            "file_id": self.file_id,
            "file_name": self.file_name,
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True, slots=True)
class ReelDraft:
    account_alias: str
    caption: str
    video: InstagramFile
    expires_at: datetime

    @classmethod
    def build(
        cls, *, account_alias: Any, caption: Any, video: Any, expires_at: datetime
    ) -> ReelDraft:
        if not isinstance(account_alias, str) or not account_alias.strip():
            raise InstagramContractError("account_alias is required")
        if not isinstance(caption, str):
            raise InstagramContractError("caption must be text")
        caption = caption.strip()
        if len(caption) > MAX_CAPTION_LENGTH:
            raise InstagramContractError(
                f"caption exceeds {MAX_CAPTION_LENGTH} characters"
            )
        return cls(
            account_alias=account_alias.strip(),
            caption=caption,
            video=InstagramFile.from_mapping(video),
            expires_at=expires_at.astimezone(UTC),
        )


@dataclass(frozen=True, slots=True)
class PublishedReel:
    media_id: str
    permalink: str | None
    state: PublishState


@dataclass(frozen=True, slots=True)
class ReelInsights:
    media_id: str
    captured_at: datetime
    metrics: dict[str, int | float | None]