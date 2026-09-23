"""MTProto readiness must not inherit the Bot API Business send flag."""

from app.application.personal_telegram_status import personal_telegram_server_status


def test_server_status_does_not_claim_local_session_is_live():
    result = personal_telegram_server_status()
    assert result["configured"] is None
    assert result["authenticated"] is None
    assert result["runtime_connected"] is False
    assert result["send_enabled"] is False
    assert result["create_group_enabled"] is False
    assert result["last_successful_check"] is None
    assert result["error_code"] == "local_session_not_bound"
