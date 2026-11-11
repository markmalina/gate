"""Server-owned W1 admission. No client selector, provider or persistent state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.models.watch_voice import WatchVoiceNote


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("watch_voice_w1_admission_invalid")
        values[key] = value
    return values


def _time(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("watch_voice_w1_admission_invalid")
    return stamp.astimezone(UTC)


@dataclass(frozen=True, repr=False)
class W1Admission:
    event_id: str
    workspace_key: str
    account_id: str
    file_id: str
    revision: str
    sha256: str
    gate_sha: str
    not_before: datetime
    expires_at: datetime

    @classmethod
    def from_settings(cls, settings: Settings) -> W1Admission:
        raw = settings.gate_watch_voice_w1_admission.get_secret_value()
        if not raw or len(raw.encode("utf-8")) > 4096:
            raise ValueError("watch_voice_w1_admission_invalid")
        value = json.loads(raw, object_pairs_hook=_closed_object)
        fields = {
            "schema",
            "event_id",
            "workspace_key",
            "account_id",
            "file_id",
            "revision",
            "sha256",
            "gate_sha",
            "not_before",
            "expires_at",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or any(
                not isinstance(item, str) or not item or item != item.strip() or len(item) > 255
                for item in value.values()
            )
            or value["schema"] != "watch-voice-w1-admission.v1"
            or str(UUID(value["event_id"])) != value["event_id"]
            or value["workspace_key"] != settings.gate_workspace_id
            or value["account_id"] != settings.dropbox_call_account_id
            or re.fullmatch(r"id:[A-Za-z0-9_-]{1,252}", value["file_id"]) is None
            or re.fullmatch(r"[a-f0-9]{1,255}", value["revision"]) is None
            or re.fullmatch(r"[a-f0-9]{64}", value["sha256"]) is None
            or re.fullmatch(r"[a-f0-9]{40}", value["gate_sha"]) is None
            or value["gate_sha"] != settings.deployment_sha
        ):
            raise ValueError("watch_voice_w1_admission_invalid")
        return cls(
            **{key: value[key] for key in fields - {"schema", "not_before", "expires_at"}},
            not_before=_time(value["not_before"]),
            expires_at=_time(value["expires_at"]),
        )

    def active(self) -> bool:
        return self.not_before <= datetime.now(UTC) < self.expires_at

    def matches(self, note: WatchVoiceNote) -> bool:
        return (
            (note.id, note.workspace_key, note.account_id, note.file_id, note.revision, note.sha256)
            == (
                self.event_id,
                self.workspace_key,
                self.account_id,
                self.file_id,
                self.revision,
                self.sha256,
            )
            and 0 < note.size_bytes <= 25 * 1024 * 1024
            and len(note.data) == note.size_bytes
            and hashlib.sha256(note.data).hexdigest() == self.sha256
            and note.duration_seconds is not None
            and 0 < note.duration_seconds <= 3600
        )
