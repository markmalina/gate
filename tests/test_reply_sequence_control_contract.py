"""No provider/database effects: protected-input and durable transport contract."""

from types import SimpleNamespace

import httpx
import pytest
from app.services.telegram import TelegramApiError, TelegramClient
from app.services.telegram_delivery_gate import (
    CanaryDelivery,
    canary_delivery,
    register_gate,
    unregister_gate,
)
from app.worker.reply_sequence_control import parse_canary_arm


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("raw", ["{}", "[]", "x" * 65537, '{"grants":[{}]}'])
def test_protected_scope_is_bounded_and_rejects_missing_fields(raw):
    with pytest.raises(ValueError):
        parse_canary_arm(raw)


@pytest.mark.anyio
async def test_bound_notification_still_requires_canary_reservation_when_flag_is_off(monkeypatch):
    import app.core.config as config

    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(gate_reply_sequence_canary_enabled=False)
    )
    calls = []

    class Gate:
        async def before(self, payload):
            import time

            return time.monotonic()

        async def reserve_canary(self, intent, payload):
            assert intent.execution_id == "closed-execution"
            raise TelegramApiError(error_code=403, description="Canary admission closed")

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    gate = Gate()
    register_gate("123", gate)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            telegram = TelegramClient("123:synthetic", http_client=client)
            with (
                canary_delivery(
                    CanaryDelivery(
                        kind="notification",
                        principal_subject="owner",
                        account_id=None,
                        dialog_ref=None,
                        body_sha256=None,
                        destination="999",
                        execution_id="closed-execution",
                    )
                ),
                pytest.raises(TelegramApiError),
            ):
                await telegram.send_plain_message(chat_id="999", text="synthetic")
        assert not calls
    finally:
        unregister_gate("123", gate)
