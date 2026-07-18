"""Pure parity checks for ordinary Telegram Business text."""

import pytest
from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    WorkRoute,
)
from app.application.telegram_business_text_parity import (
    TelegramBusinessLegacyTextFact,
    TelegramBusinessTextParityCommand,
    TelegramBusinessTextParityError,
    compare_telegram_business_text_parity,
)


def _command(
    *,
    direction: CanonicalMessageDirection = CanonicalMessageDirection.INBOUND,
    route: WorkRoute = WorkRoute.HUMAN_REPLY_TASK,
) -> TelegramBusinessTextParityCommand:
    return TelegramBusinessTextParityCommand(
        update_id=42,
        account_alias="telegram_mark_ru",
        direction=direction,
        route=route,
        provider_dialog_id="private-dialog",
        business_connection_id="private-connection",
        text_content="private-text",
    )


def _legacy(
    *,
    direction: str = "inbound",
    reply_task_count: int = 1,
) -> TelegramBusinessLegacyTextFact:
    return TelegramBusinessLegacyTextFact(
        account_alias="telegram_mark_ru",
        direction=direction,
        reply_task_count=reply_task_count,
        provider_dialog_id="private-dialog",
        business_connection_id="private-connection",
        text_content="private-text",
    )


def test_inbound_text_requires_exact_legacy_reply_task_parity() -> None:
    result = compare_telegram_business_text_parity(_command(), _legacy())

    assert result.disposition == "matched"
    assert "private" not in repr(result)


def test_owner_outbound_text_requires_no_reply_task() -> None:
    result = compare_telegram_business_text_parity(
        _command(
            direction=CanonicalMessageDirection.OUTBOUND,
            route=WorkRoute.IGNORED,
        ),
        _legacy(direction="outbound", reply_task_count=0),
    )

    assert result.reply_task_matched is True


@pytest.mark.parametrize(
    ("legacy", "error_code"),
    [
        (
            TelegramBusinessLegacyTextFact(
                account_alias="telegram_mark_by",
                direction="inbound",
                reply_task_count=1,
                provider_dialog_id="private-dialog",
                business_connection_id="private-connection",
                text_content="private-text",
            ),
            "telegram_business_text_parity_account_mismatch",
        ),
        (
            _legacy(direction="outbound"),
            "telegram_business_text_parity_direction_mismatch",
        ),
        (
            TelegramBusinessLegacyTextFact(
                account_alias="telegram_mark_ru",
                direction="inbound",
                reply_task_count=1,
                provider_dialog_id="other-dialog",
                business_connection_id="private-connection",
                text_content="private-text",
            ),
            "telegram_business_text_parity_dialog_mismatch",
        ),
        (
            TelegramBusinessLegacyTextFact(
                account_alias="telegram_mark_ru",
                direction="inbound",
                reply_task_count=1,
                provider_dialog_id="private-dialog",
                business_connection_id="private-connection",
                text_content="different-text",
            ),
            "telegram_business_text_parity_text_mismatch",
        ),
        (
            _legacy(reply_task_count=0),
            "telegram_business_text_parity_reply_task_mismatch",
        ),
    ],
)
def test_private_mismatches_fail_with_bounded_codes(
    legacy: TelegramBusinessLegacyTextFact,
    error_code: str,
) -> None:
    with pytest.raises(TelegramBusinessTextParityError) as captured:
        compare_telegram_business_text_parity(_command(), legacy)

    assert captured.value.code == error_code
    assert "private" not in str(captured.value)
    assert "private-text" not in repr(legacy)
