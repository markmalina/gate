"""One literal synthetic recipe; there is no caller-selected dataset."""

from datetime import UTC, datetime
from uuid import UUID

from app.worker.gate745_fixture_policy import digest

STAMP = datetime(2026, 1, 1, tzinfo=UTC)
BOT, PROJECT, ACCOUNT, CONVERSATION, MESSAGE, TASK, DRAFT, ATTEMPT = (
    UUID(f"b527b8a3-2c7a-4bd6-92b3-f5244c74c{i:03d}") for i in range(101, 109)
)
LABEL = "gate745-synthetic"
TIMES = {"created_at": STAMP, "updated_at": STAMP}
ROWS: dict[str, dict[str, object]] = {
    "bots": {"id": BOT, "username": LABEL, "token_secret_ref": None, "is_active": False, **TIMES},
    "projects": {
        "id": PROJECT,
        "alias": LABEL,
        "name": LABEL,
        "adapter_type": "synthetic",
        "memory_target": None,
        "is_active": False,
        **TIMES,
    },
    "accounts": {
        "id": ACCOUNT,
        "alias": LABEL,
        "owner": "gate745-fixture",
        "account_type": "synthetic",
        "linked_project_id": PROJECT,
        "permissions": {},
        "reply_mode": "manual",
        "audit_log_enabled": True,
        "read_enabled": False,
        "send_enabled": False,
        "is_active": False,
        **TIMES,
    },
    "conversations": {
        "id": CONVERSATION,
        "bot_id": BOT,
        "account_id": ACCOUNT,
        "project_id": PROJECT,
        "chat_id": LABEL,
        "title": "Synthetic fixture",
        "status": "open",
        "requires_reply": False,
        "last_message_at": STAMP,
        "last_inbound_at": STAMP,
        **TIMES,
    },
    "messages": {
        "id": MESSAGE,
        "bot_id": BOT,
        "account_id": ACCOUNT,
        "project_id": PROJECT,
        "direction": "inbound",
        "source": "gate745-fixture",
        "event_type": "synthetic",
        "chat_id": LABEL,
        "sender_id": LABEL,
        "text": "Literal synthetic fixture input",
        "payload": {},
        "received_at": STAMP,
        "created_at": STAMP,
    },
    "reply_tasks": {
        "id": TASK,
        "account_id": ACCOUNT,
        "project_id": PROJECT,
        "conversation_id": CONVERSATION,
        "trigger_message_id": MESSAGE,
        "status": "needs_reply",
        "priority": 100,
        "reason": "Synthetic fixture",
        **TIMES,
    },
    "reply_drafts": {
        "id": DRAFT,
        "account_id": ACCOUNT,
        "project_id": PROJECT,
        "reply_task_id": TASK,
        "conversation_id": CONVERSATION,
        "trigger_message_id": MESSAGE,
        "status": "generated",
        "source": "gate745-fixture",
        "draft_text": "Literal synthetic fixture draft",
        "prompt_context": {},
        **TIMES,
    },
    "send_attempts": {
        "id": ATTEMPT,
        "reply_draft_id": DRAFT,
        "reply_task_id": TASK,
        "conversation_id": CONVERSATION,
        "account_id": ACCOUNT,
        "project_id": PROJECT,
        "principal_subject": "gate745-fixture",
        "status": "unknown",
        "account_label": "synthetic",
        "target_chat_id": LABEL,
        "business_connection_id": "gate745-synthetic-disabled",
        "provider_message_id": None,
        "error_code": "synthetic-provider-unknown",
        "error_detail": None,
        "reserved_at": STAMP,
        "started_at": STAMP,
        "finished_at": STAMP,
        **TIMES,
    },
}


def canonical(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return value


RECIPE_DIGEST = digest(canonical(ROWS))
JOURNAL_DIGEST = digest(canonical([ROWS["send_attempts"]]))
EMPTY_JOURNAL_DIGEST = digest([])
