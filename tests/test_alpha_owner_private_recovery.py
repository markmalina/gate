"""Shape guards for the one-shot owner-private Alpha recovery."""

from app.infrastructure.alpha_owner_private_recovery import owner_private_sender


def _update(*, chat_id: int = 77, sender_id: int = 77) -> dict[str, object]:
    return {
        "update_id": 91,
        "message": {
            "message_id": 92,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": sender_id, "is_bot": False},
            "text": "owner command",
        },
    }


def test_owner_private_sender_accepts_only_self_addressed_human_message() -> None:
    assert owner_private_sender(_update()) == "77"

    assert owner_private_sender(_update(chat_id=78)) is None
    bot_update = _update()
    bot_update["message"]["from"]["is_bot"] = True  # type: ignore[index]
    assert owner_private_sender(bot_update) is None


def test_owner_private_sender_rejects_mixed_or_group_updates() -> None:
    mixed = _update()
    mixed["callback_query"] = {"id": "unexpected"}
    assert owner_private_sender(mixed) is None

    group = _update()
    group["message"]["chat"]["type"] = "group"  # type: ignore[index]
    assert owner_private_sender(group) is None
