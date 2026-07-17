"""Provider command contracts and a deterministic pre-connection executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
from pathlib import Path
from typing import Any


class InstagramCommandError(ValueError):
    """Invalid or unsafe MAYAKI to Gate command."""


class InstagramCommandType(StrEnum):
    PUBLIC_REPLY = "gate.instagram.public_reply.requested.v1"
    PRIVATE_REPLY = "gate.instagram.private_reply.requested.v1"
    FOLLOW_CHECK = "gate.instagram.follow_check.requested.v1"
    FOLLOW_PROMPT = "gate.instagram.follow_prompt.requested.v1"


class ProviderFollowState(StrEnum):
    FOLLOWING = "following"
    NOT_FOLLOWING = "not_following"
    UNKNOWN = "unknown"
    NOT_PERMITTED = "not_permitted"


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InstagramCommandError(f"{field}_required")
    return value.strip()


@dataclass(frozen=True, slots=True)
class InstagramProviderCommand:
    command_type: InstagramCommandType
    idempotency_key: str
    account_alias: str
    provider_comment_id: str | None
    instagram_user_id: str | None
    text: str | None
    quick_reply: dict[str, str] | None

    @classmethod
    def from_mapping(cls, value: Any) -> InstagramProviderCommand:
        if not isinstance(value, dict):
            raise InstagramCommandError("command_required")
        raw_type = value.get("type")
        if not isinstance(raw_type, str):
            raise InstagramCommandError("command_type_invalid")
        try:
            command_type = InstagramCommandType(raw_type)
        except ValueError as exc:
            raise InstagramCommandError("command_type_invalid") from exc
        provider_comment_id = value.get("provider_comment_id")
        instagram_user_id = value.get("instagram_user_id")
        text = value.get("text")
        quick_reply = value.get("quick_reply")
        if command_type in {
            InstagramCommandType.PUBLIC_REPLY,
            InstagramCommandType.PRIVATE_REPLY,
        }:
            provider_comment_id = _required_text(provider_comment_id, "provider_comment_id")
        if command_type in {
            InstagramCommandType.FOLLOW_CHECK,
            InstagramCommandType.FOLLOW_PROMPT,
        }:
            instagram_user_id = _required_text(instagram_user_id, "instagram_user_id")
        if command_type is not InstagramCommandType.FOLLOW_CHECK:
            text = _required_text(text, "text")
            if len(text) > 1000:
                raise InstagramCommandError("text_too_long")
        if quick_reply is not None:
            if not isinstance(quick_reply, dict):
                raise InstagramCommandError("quick_reply_invalid")
            quick_reply = {
                "title": _required_text(quick_reply.get("title"), "quick_reply_title"),
                "payload": _required_text(quick_reply.get("payload"), "quick_reply_payload"),
            }
        return cls(
            command_type=command_type,
            idempotency_key=_required_text(value.get("idempotency_key"), "idempotency_key"),
            account_alias=_required_text(value.get("account_alias"), "account_alias"),
            provider_comment_id=provider_comment_id,
            instagram_user_id=instagram_user_id,
            text=text,
            quick_reply=quick_reply,
        )


class FakeInstagramCommandExecutor:
    """Exercises contracts without calling Meta; never used as delivery proof."""

    def __init__(self, *, follow_state: ProviderFollowState = ProviderFollowState.UNKNOWN) -> None:
        self.follow_state = follow_state
        self._results: dict[str, dict[str, Any]] = {}

    def execute(self, value: Any) -> dict[str, Any]:
        command = InstagramProviderCommand.from_mapping(value)
        previous = self._results.get(command.idempotency_key)
        if previous is not None:
            return {**previous, "idempotent_replay": True}
        result: dict[str, Any] = {
            "status": "simulated",
            "command_type": command.command_type.value,
            "idempotency_key": command.idempotency_key,
            "idempotent_replay": False,
            "external_delivery": False,
        }
        if command.command_type is InstagramCommandType.FOLLOW_CHECK:
            result["follow_state"] = self.follow_state.value
        self._results[command.idempotency_key] = result
        return result


class JsonInstagramCommandResultStore:
    """Small atomic replay store for one supervised Gate instance."""

    def __init__(self, path: str) -> None:
        resolved = Path(path).resolve()
        if not path.strip() or resolved == Path(resolved.anchor) or resolved == Path.home():
            raise InstagramCommandError("command_result_store_path_unsafe")
        self.path = resolved

    def get(self, idempotency_key: str) -> dict[str, Any] | None:
        return self._load().get(idempotency_key)

    def put(self, idempotency_key: str, result: dict[str, Any]) -> None:
        data = self._load()
        data[idempotency_key] = result
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InstagramCommandError("command_result_store_invalid") from exc
        if not isinstance(payload, dict):
            raise InstagramCommandError("command_result_store_invalid")
        return payload
