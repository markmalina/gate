from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.application.universal_mayak_demo import (
    DemoDisposition,
    DemoScope,
    UniversalMayakDemoResult,
)
from app.application.universal_mayak_private_ingress import (
    ApprovedPrivateBinding,
    PrivateIngressDisposition,
    ProcessUniversalMayakPrivateUpdateCommand,
    ProcessUniversalMayakPrivateUpdateService,
    UniversalMayakPrivateIngressError,
)

NOW = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)
SCOPE = DemoScope("workspace_owner", "installation_demo", "opaque_dialog")


class Bindings:
    def __init__(self, binding: ApprovedPrivateBinding | None):
        self.binding = binding
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, *, provider_dialog_id, provider_actor_id):
        self.calls.append((provider_dialog_id, provider_actor_id))
        return self.binding


class Mayak:
    def __init__(self):
        self.calls = []

    async def execute(self, command):
        self.calls.append(command)
        return UniversalMayakDemoResult(
            DemoDisposition.SENT,
            "mayak_demo_sent",
            "turn_1",
            "delivery_1",
        )


def update(*, text="/start", chat_type="private", actor_is_bot=False):
    message = {
        "message_id": 20,
        "from": {"id": 30, "is_bot": actor_is_bot},
        "chat": {"id": 40, "type": chat_type},
    }
    if text is not None:
        message["text"] = text
    return {"update_id": 10, "message": message}


def command(payload=None, **overrides):
    values = {
        "update": payload or update(),
        "runtime_enabled": True,
        "connector_enabled": True,
        "credentials_ready": True,
        "now": NOW,
    }
    values.update(overrides)
    return ProcessUniversalMayakPrivateUpdateCommand(**values)


@pytest.mark.anyio
async def test_default_off_blocks_before_identity_resolution() -> None:
    bindings = Bindings(ApprovedPrivateBinding(SCOPE, "owner", True))
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateUpdateService(
        bindings=bindings,
        mayak=mayak,
    )

    result = await service.execute(command(runtime_enabled=False))

    assert result.disposition is PrivateIngressDisposition.DISABLED
    assert result.code == "mayak_private_runtime_disabled"
    assert bindings.calls == []
    assert mayak.calls == []


@pytest.mark.anyio
async def test_approved_private_text_maps_to_one_exact_command() -> None:
    bindings = Bindings(ApprovedPrivateBinding(SCOPE, "owner", True))
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateUpdateService(
        bindings=bindings,
        mayak=mayak,
    )

    result = await service.execute(command())

    assert result.disposition is PrivateIngressDisposition.PROCESSED
    assert bindings.calls == [("40", "30")]
    assert len(mayak.calls) == 1
    mapped = mayak.calls[0]
    assert mapped.scope == SCOPE
    assert mapped.event_ref == "telegram_update_10"
    assert mapped.idempotency_key == "telegram_message_40_20"
    assert mapped.invocation.value == "start"
    assert mapped.text == "/start"
    assert mapped.actor_scope == "owner"


@pytest.mark.anyio
async def test_unapproved_binding_never_reaches_model_path() -> None:
    bindings = Bindings(None)
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateUpdateService(
        bindings=bindings,
        mayak=mayak,
    )

    result = await service.execute(command())

    assert result.disposition is PrivateIngressDisposition.BLOCKED
    assert result.code == "mayak_private_binding_not_active"
    assert mayak.calls == []


@pytest.mark.anyio
async def test_non_text_private_update_is_ignored() -> None:
    bindings = Bindings(ApprovedPrivateBinding(SCOPE, "owner", True))
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateUpdateService(
        bindings=bindings,
        mayak=mayak,
    )

    result = await service.execute(command(update(text=None)))

    assert result.disposition is PrivateIngressDisposition.NOT_APPLICABLE
    assert bindings.calls == []
    assert mayak.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"update_id": 10, "edited_message": {}},
        update(chat_type="group"),
        update(actor_is_bot=True),
        {"update_id": -1, "message": {}},
    ],
)
async def test_unsupported_or_unsafe_updates_fail_closed(payload) -> None:
    bindings = Bindings(ApprovedPrivateBinding(SCOPE, "owner", True))
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateUpdateService(
        bindings=bindings,
        mayak=mayak,
    )

    with pytest.raises(UniversalMayakPrivateIngressError):
        await service.execute(command(payload))

    assert bindings.calls == []
    assert mayak.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/pause", "pause"),
        ("/human", "human_takeover"),
        ("Мне нужна помощь", "direct"),
    ],
)
async def test_commands_are_mapped_before_the_model(text, expected) -> None:
    bindings = Bindings(ApprovedPrivateBinding(SCOPE, "owner", True))
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateUpdateService(
        bindings=bindings,
        mayak=mayak,
    )

    await service.execute(command(update(text=text)))

    assert mayak.calls[0].invocation.value == expected
