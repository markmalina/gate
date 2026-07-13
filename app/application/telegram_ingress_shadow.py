"""Side-effect-free canonical classification for Telegram ingress shadowing."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

JsonObject = dict[str, Any]

SPRINT18_DRAFT = "app.api.sprint18b_draft_ux_routes:sprint18b_telegram_webhook"
SPRINT17_SEND = "app.api.sprint17_send_draft_routes:sprint17_telegram_webhook"
SPRINT16_INBOX = "app.api.sprint16d_clean_inbox_routes:sprint16d_telegram_webhook"
SPRINT16_RAW = "app.api.sprint16_raw_routes:sprint16_telegram_webhook"
SPRINT15_DIAGNOSTICS = "app.api.sprint15b_routes:sprint15b_telegram_webhook"
SPRINT14_BUSINESS_ADMIN = "app.api.sprint14b_routes:sprint14b_telegram_webhook"
SPRINT14_BUSINESS = "app.api.sprint14_routes:sprint14_telegram_webhook"
SPRINT11_CALLBACK = "app.api.sprint11b_routes:sprint11b_telegram_webhook"
SPRINT10_EDIT = "app.api.sprint10_routes:sprint10_telegram_webhook"
SPRINT9_LEGACY_ADMIN = "app.api.sprint9_routes:sprint9_telegram_webhook"
BASE_WEBHOOK = "app.api.routes:telegram_webhook"


@dataclass(frozen=True)
class TelegramIngressDecision:
    """Redacted decision emitted by the canonical shadow classifier."""

    handler: str
    reason: str
    update_id: int | None
    update_type: str
    event_type: str | None
    command: str | None
    business_connection_present: bool

    def log_fields(self) -> JsonObject:
        """Return a stable redacted mapping suitable for structured logs."""

        return asdict(self)


def classify_telegram_ingress(update: JsonObject) -> TelegramIngressDecision:
    """Select the canonical handler without I/O, persistence or Telegram sends."""

    update_id = _optional_int(update.get("update_id"))
    update_type = _update_type(update)
    event_type, text, business_connection_present = _message_shape(update)

    callback_command = _callback_command(update)
    if callback_command is not None:
        return _decision(
            handler=SPRINT11_CALLBACK,
            reason="gate_draft_callback",
            update_id=update_id,
            update_type=update_type,
            event_type="callback_query",
            command=callback_command,
            business_connection_present=business_connection_present,
        )

    if event_type is None:
        return _decision(
            handler=SPRINT16_RAW,
            reason="unsupported_update_shape",
            update_id=update_id,
            update_type=update_type,
            event_type=None,
            command=None,
            business_connection_present=business_connection_present,
        )

    normalized = (text or "").strip().casefold()
    command = _command_kind(normalized)

    if command == "draft":
        handler, reason = SPRINT18_DRAFT, "draft_command"
    elif command == "send":
        handler, reason = SPRINT17_SEND, "approved_draft_send_command"
    elif command in {"inbox", "clean_inbox_tests", "clean_inbox_service"}:
        handler, reason = SPRINT16_INBOX, "scoped_inbox_command"
    elif command == "raw":
        handler, reason = SPRINT16_RAW, "raw_diagnostics_command"
    elif command in {"events", "webhook", "webhook_drop_pending"}:
        handler, reason = SPRINT15_DIAGNOSTICS, "diagnostics_command"
    elif command in {
        "business_assignment",
        "status",
        "drafts",
        "done",
        "dismiss",
        "snooze",
        "reopen",
        "test",
    }:
        handler, reason = SPRINT14_BUSINESS_ADMIN, "business_admin_command"
    elif _is_business_update(update):
        handler, reason = SPRINT14_BUSINESS, "business_update"
    elif command == "edit":
        handler, reason = SPRINT10_EDIT, "draft_edit_command"
    elif command == "legacy_send":
        handler, reason = SPRINT9_LEGACY_ADMIN, "legacy_send_command"
    else:
        handler, reason = BASE_WEBHOOK, "base_message"

    return _decision(
        handler=handler,
        reason=reason,
        update_id=update_id,
        update_type=update_type,
        event_type=event_type,
        command=command,
        business_connection_present=business_connection_present,
    )


def observe_telegram_ingress_shadow(
    update: JsonObject,
    *,
    logger: logging.Logger,
) -> TelegramIngressDecision:
    """Log one redacted canonical decision and return it for tests/metrics."""

    decision = classify_telegram_ingress(update)
    logger.info("Telegram ingress shadow decision: %s", decision.log_fields())
    return decision


def _decision(
    *,
    handler: str,
    reason: str,
    update_id: int | None,
    update_type: str,
    event_type: str | None,
    command: str | None,
    business_connection_present: bool,
) -> TelegramIngressDecision:
    return TelegramIngressDecision(
        handler=handler,
        reason=reason,
        update_id=update_id,
        update_type=update_type,
        event_type=event_type,
        command=command,
        business_connection_present=business_connection_present,
    )


def _message_shape(update: JsonObject) -> tuple[str | None, str | None, bool]:
    for key in ("message", "business_message"):
        value = update.get(key)
        if not isinstance(value, dict):
            continue
        text = value.get("text")
        return (
            key,
            text if isinstance(text, str) else None,
            key == "business_message" or value.get("business_connection_id") is not None,
        )
    connection = update.get("business_connection")
    if isinstance(connection, dict):
        return "business_connection", None, True
    return None, None, False


def _update_type(update: JsonObject) -> str:
    types = sorted(str(key) for key in update if key != "update_id")
    return ",".join(types) or "unknown"


def _callback_command(update: JsonObject) -> str | None:
    callback = update.get("callback_query")
    if not isinstance(callback, dict):
        return None
    data = callback.get("data")
    if not isinstance(data, str):
        return None
    parts = data.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != "gate":
        return None
    action, draft_ref = parts[1], parts[2].strip()
    if action not in {"edit", "send"} or len(draft_ref) < 4:
        return None
    return f"callback_{action}"


def _command_kind(text: str) -> str | None:
    if _has_ref(text, prefixes=("/draft_",), words=("/draft", "draft")):
        return "draft"
    if _has_ref(
        text,
        prefixes=("/send_", "/approve_"),
        words=("/send", "send", "/approve", "approve"),
    ):
        return "send"
    if text in {"/inbox", "inbox", "инбокс", "входящие"}:
        return "inbox"
    if text in {"/clean_inbox_tests", "clean_inbox_tests", "/clean_tests", "clean_tests"}:
        return "clean_inbox_tests"
    if text in {
        "/clean_inbox_service",
        "clean_inbox_service",
        "/clean_service",
        "clean_service",
    }:
        return "clean_inbox_service"
    if text in {"/raw_updates", "raw_updates", "/raw", "raw", "/сырые", "сырые"}:
        return "raw"
    if text in {"/events", "events", "/messages", "messages", "события", "сообщения"}:
        return "events"
    if text in {"/webhook", "webhook", "/вебхук", "вебхук"}:
        return "webhook"
    if text in {
        "/webhook_drop_pending",
        "webhook_drop_pending",
        "/webhook_reset",
        "webhook_reset",
        "/webhook_drop",
        "webhook_drop",
    }:
        return "webhook_drop_pending"
    if _is_business_assignment(text):
        return "business_assignment"
    if text in {"/status", "status", "статус"}:
        return "status"
    for name in ("drafts", "done", "dismiss", "snooze", "reopen"):
        if text in {f"/{name}", name} or text.startswith(f"/{name}_"):
            return name
    if text in {"/test", "test"}:
        return "test"
    if _has_ref(
        text,
        prefixes=("/edit_", "/editdraft_"),
        words=("/edit", "edit", "/editdraft", "editdraft"),
    ):
        return "edit"
    if _has_ref(
        text,
        prefixes=("/senddraft_",),
        words=("/senddraft", "senddraft"),
    ):
        return "legacy_send"
    return None


def _has_ref(
    text: str,
    *,
    prefixes: tuple[str, ...],
    words: tuple[str, ...],
) -> bool:
    if any(text.startswith(prefix) and bool(text.removeprefix(prefix).strip()) for prefix in prefixes):
        return True
    parts = text.split(maxsplit=1)
    return len(parts) == 2 and parts[0] in words and bool(parts[1].strip())


def _is_business_assignment(text: str) -> bool:
    parts = text.split(maxsplit=2)
    aliases = {"ru", "ру", "telegram_mark_ru", "by", "бу", "бел", "telegram_mark_by"}
    if len(parts) == 3 and parts[0] == "/business" and parts[1] in aliases:
        return bool(parts[2].strip())
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0] in {"/business_ru", "/business_by"}:
        return bool(parts[1].strip())
    return any(
        text.startswith(prefix) and bool(text.removeprefix(prefix).strip())
        for prefix in ("/business_ru_", "/business_by_")
    )


def _is_business_update(update: JsonObject) -> bool:
    if isinstance(update.get("business_connection"), dict):
        return True
    if isinstance(update.get("business_message"), dict):
        return True
    message = update.get("message")
    return isinstance(message, dict) and message.get("business_connection_id") is not None


def _optional_int(value: Any) -> int | None:
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None
