from uuid import UUID

import httpx
import pytest
from app.application.reply_sends import (
    ReplySendError,
    ReplySendResult,
    SendApprovedReplyCommand,
    SendApprovedReplyService,
)
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.reply_sends import _delivery_failure, _safe_error_detail
from app.services.telegram import TelegramApiError

DRAFT_ID = UUID("11111111-1111-1111-1111-111111111111")
TASK_ID = UUID("22222222-2222-2222-2222-222222222222")
ACCOUNT_ID = UUID("33333333-3333-3333-3333-333333333333")
PROJECT_ID = UUID("44444444-4444-4444-4444-444444444444")
ATTEMPT_ID = UUID("55555555-5555-5555-5555-555555555555")


class CapturingExecutor:
    def __init__(self) -> None:
        self.commands: list[SendApprovedReplyCommand] = []

    async def send(self, command: SendApprovedReplyCommand) -> ReplySendResult:
        self.commands.append(command)
        return ReplySendResult(
            attempt_id=ATTEMPT_ID,
            draft_id=command.draft_id,
            task_id=TASK_ID,
            account_id=ACCOUNT_ID,
            project_id=PROJECT_ID,
            account_alias="telegram_mark_ru",
            account_label="RU",
            recipient_chat_id="12345",
            status="sent",
            sent=True,
            idempotent_replay=False,
            provider_message_id="777",
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _principal(
    *, scopes: frozenset[str], explicit: bool = True
) -> Principal:
    return Principal(
        subject="user-123",
        auth_method="oauth2",
        scopes=scopes,
        access_scope=AccessScope(
            workspace_id="mark-personal" if explicit else "",
            account_ids=frozenset({ACCOUNT_ID}),
            project_ids=frozenset({PROJECT_ID}),
        ),
    )


def _command(
    *,
    scopes: frozenset[str] = frozenset({"gate:messages:send"}),
    confirmed: bool = True,
    expected_account: str = "RU",
    recipient: str = "12345",
) -> SendApprovedReplyCommand:
    return SendApprovedReplyCommand(
        principal=_principal(scopes=scopes),
        draft_id=DRAFT_ID,
        confirmed=confirmed,
        expected_account=expected_account,
        expected_recipient_chat_id=recipient,
    )


@pytest.mark.anyio
async def test_send_requires_scope_and_explicit_confirmation() -> None:
    executor = CapturingExecutor()
    service = SendApprovedReplyService(executor)

    with pytest.raises(ReplySendError) as no_scope:
        await service.execute(_command(scopes=frozenset({"gate:drafts:write"})))
    assert no_scope.value.code == "insufficient_scope"

    with pytest.raises(ReplySendError) as no_confirmation:
        await service.execute(_command(confirmed=False))
    assert no_confirmation.value.code == "confirmation_required"
    assert executor.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("account", "recipient"),
    [("", "12345"), ("DE", "12345"), ("RU", ""), ("BY", "   ")],
)
async def test_send_rejects_invalid_account_or_recipient(
    account: str, recipient: str
) -> None:
    executor = CapturingExecutor()
    service = SendApprovedReplyService(executor)

    with pytest.raises(ReplySendError) as exc_info:
        await service.execute(
            _command(expected_account=account, recipient=recipient)
        )

    assert exc_info.value.code == "invalid_confirmation"
    assert executor.commands == []


@pytest.mark.anyio
async def test_send_normalizes_confirmation_before_delivery() -> None:
    executor = CapturingExecutor()
    service = SendApprovedReplyService(executor)

    result = await service.execute(
        _command(expected_account="  ru  ", recipient=" 12345 ")
    )

    assert result.sent is True
    assert executor.commands[0].expected_account == "RU"
    assert executor.commands[0].expected_recipient_chat_id == "12345"


def test_ambiguous_delivery_failures_are_unknown() -> None:
    request = httpx.Request("POST", "https://api.telegram.org/sendMessage")
    timeout = httpx.ReadTimeout("timed out", request=request)

    assert _delivery_failure(timeout) == ("unknown", "telegram_timeout")
    assert _delivery_failure(RuntimeError("telegram failed")) == (
        "unknown",
        "telegram_unconfirmed",
    )


def test_telegram_api_error_preserves_sanitized_diagnostics() -> None:
    error = TelegramApiError(
        error_code=400,
        description="Bad Request: business connection not found",
        http_status=400,
    )

    assert _delivery_failure(error) == ("failed", "telegram_api_400")
    assert _safe_error_detail(error) == "Bad Request: business connection not found"


def test_telegram_server_error_is_unknown() -> None:
    error = TelegramApiError(
        error_code=500,
        description="Internal Server Error",
        http_status=500,
    )

    assert _delivery_failure(error) == ("unknown", "telegram_http_500")


def test_ambiguous_http_and_transport_failures_are_unknown() -> None:
    request = httpx.Request("POST", "https://api.telegram.org/sendMessage")
    response = httpx.Response(503, request=request)
    http_error = httpx.HTTPStatusError(
        "service unavailable",
        request=request,
        response=response,
    )
    protocol_error = httpx.RemoteProtocolError(
        "connection closed before a response",
        request=request,
    )

    assert _delivery_failure(http_error) == ("unknown", "telegram_http_5xx")
    assert _delivery_failure(protocol_error) == (
        "unknown",
        "telegram_transport_unknown",
    )


def test_authoritative_telegram_rate_limit_is_failed() -> None:
    error = TelegramApiError(
        error_code=429,
        description="Too Many Requests",
        http_status=429,
    )

    assert _delivery_failure(error) == ("failed", "telegram_api_429")
