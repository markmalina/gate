from app.infrastructure.beta_wedding_binding import (
    BETA_WEDDING_ACTIVATION_TEXT,
    BETA_WEDDING_GROUP_TITLE,
    extract_beta_wedding_activation_candidate,
)


def _activation(*, title: str = BETA_WEDDING_GROUP_TITLE, first_name: str = "Марк"):
    return {
        "update_id": 496001,
        "message": {
            "message_id": 1,
            "date": 1786040000,
            "text": BETA_WEDDING_ACTIVATION_TEXT,
            "chat": {
                "id": -100496001,
                "type": "supergroup",
                "title": title,
            },
            "from": {
                "id": 496002,
                "is_bot": False,
                "first_name": first_name,
            },
        },
    }


def test_exact_wedding_activation_returns_private_candidate():
    candidate = extract_beta_wedding_activation_candidate(_activation())

    assert candidate is not None
    assert "-100496001" not in repr(candidate)
    assert "496002" not in repr(candidate)


def test_wedding_activation_rejects_wrong_group():
    assert (
        extract_beta_wedding_activation_candidate(
            _activation(title="Зубы Кати")
        )
        is None
    )


def test_wedding_activation_rejects_wrong_actor():
    assert (
        extract_beta_wedding_activation_candidate(
            _activation(first_name="Катя")
        )
        is None
    )


def test_wedding_activation_rejects_near_match_text():
    payload = _activation()
    payload["message"]["text"] = BETA_WEDDING_ACTIVATION_TEXT.rstrip(".")

    assert extract_beta_wedding_activation_candidate(payload) is None


def test_wedding_activation_rejects_bot_sender():
    payload = _activation()
    payload["message"]["from"]["is_bot"] = True

    assert extract_beta_wedding_activation_candidate(payload) is None
