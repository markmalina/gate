"""Bounded contact projection from one exact stored Telegram update.

The raw update is already held by Gate's durable ingress. This module never
imports an address book, parses a vCard, or logs contact fields.
"""

from __future__ import annotations

from typing import Any

from app.application.telegram_attachments import validated_telegram_receipt_payload

JsonObject = dict[str, Any]
TELEGRAM_SAFE_USER_ID_MAX = (1 << 52) - 1


def extract_scoped_telegram_contact(
    *payloads: JsonObject | None,
    update_id: int | None,
    event_type: str,
    telegram_message_id: int | None,
    chat_id: str,
    business_connection_id: str | None,
) -> JsonObject | None:
    """Read contact fields only from a payload matching the selected message."""

    if type(update_id) is not int:
        return None
    for payload in payloads:
        exact = validated_telegram_receipt_payload(
            payload,
            receipt_update_id=update_id,
            message_update_id=update_id,
            event_type=event_type,
            telegram_message_id=telegram_message_id,
            chat_id=chat_id,
            business_connection_id=business_connection_id,
        )
        if exact is None:
            continue
        raw_message = exact[event_type]
        raw_contact = raw_message.get("contact")
        if not isinstance(raw_contact, dict):
            continue
        phone = _bounded_text(raw_contact.get("phone_number"), 64)
        first_name = _bounded_text(raw_contact.get("first_name"), 255)
        if phone is None or first_name is None:
            continue
        raw_user_id = raw_contact.get("user_id")
        user_id = (
            str(raw_user_id)
            if type(raw_user_id) is int
            and 0 < raw_user_id <= TELEGRAM_SAFE_USER_ID_MAX
            else None
        )
        return {
            "first_name": first_name,
            "last_name": _bounded_text(raw_contact.get("last_name"), 255),
            "phone_number": phone,
            "user_id": user_id,
            # A vCard can contain unrelated personal data. Gate retains the
            # existing provider receipt but does not parse or return its body.
            "vcard_present": isinstance(raw_contact.get("vcard"), str)
            and bool(raw_contact["vcard"]),
        }
    return None


def _bounded_text(value: object, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized
