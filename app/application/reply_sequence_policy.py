"""Deterministic approval and inbound-envelope rules shared by #718 and #716."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def approval_digest(
    *,
    workspace: str,
    account: UUID,
    project: UUID,
    task: UUID,
    conversation: UUID,
    recipient: str,
    revision: int,
    drafts: list[tuple[UUID, str]],
) -> str:
    value = {
        "schema": "gate.reply-sequence-approval.v1",
        "workspace_ref": workspace,
        "account_id": str(account),
        "project_id": str(project),
        "task_id": str(task),
        "conversation_id": str(conversation),
        "recipient_chat_id": recipient,
        "queued_revision": revision,
        "items": [
            {"position": i, "draft_id": str(draft), "text_sha256": digest}
            for i, (draft, digest) in enumerate(drafts)
        ],
    }
    return text_digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


@dataclass(frozen=True, slots=True)
class BusinessEnvelope:
    connection_id: str
    chat_id: str
    message_id: str
    sender_id: int | None
    sender_is_bot: bool
    digest: str


def business_envelope(payload: dict[str, Any]) -> BusinessEnvelope | None:
    message = payload.get("business_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    chat_id, message_id = chat.get("id"), message.get("message_id")
    connection = message.get("business_connection_id")
    if (
        type(chat_id) is not int
        or chat_id <= 0
        or type(message_id) is not int
        or message_id <= 0
        or not isinstance(connection, str)
        or not connection
    ):
        return None
    service_fields = {
        "new_chat_members",
        "left_chat_member",
        "new_chat_title",
        "new_chat_photo",
        "delete_chat_photo",
        "group_chat_created",
        "supergroup_chat_created",
        "channel_chat_created",
        "message_auto_delete_timer_changed",
        "pinned_message",
        "migrate_to_chat_id",
        "migrate_from_chat_id",
        "successful_payment",
        "refunded_payment",
        "write_access_allowed",
        "connected_website",
        "video_chat_started",
        "video_chat_ended",
        "video_chat_scheduled",
        "video_chat_participants_invited",
        "proximity_alert_triggered",
    }
    if service_fields.intersection(message):
        return None
    sender = message.get("from")
    sender = sender if isinstance(sender, dict) else {}
    sender_id = sender.get("id")
    return BusinessEnvelope(
        connection,
        str(chat_id),
        str(message_id),
        sender_id if type(sender_id) is int else None,
        sender.get("is_bot") is True,
        text_digest(json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    )
