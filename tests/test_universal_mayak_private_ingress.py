from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    WorkRoute,
)
from app.application.universal_mayak_demo import (
    DemoDisposition,
    DemoScope,
    UniversalMayakDemoResult,
)
from app.application.universal_mayak_private_ingress import (
    ApprovedPrivateBinding,
    PrivateIngressDisposition,
    ProcessUniversalMayakPrivateMessageCommand,
    ProcessUniversalMayakPrivateMessageService,
    UniversalMayakPrivateIngressError,
)

NOW = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)
ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
MESSAGE_ID = "33333333-3333-3333-3333-333333333333"
CONNECTION_ID = "business_connection_owner"
DIALOG_ID = "700000001"
ACTOR_ID = "700000002"
SCOPE = DemoScope("workspace_owner", "installation_owner", "opaque_dialog")


def binding(**overrides) -> ApprovedPrivateBinding:
    values = {
        "scope": SCOPE,
        "actor_scope": "owner",
        "active": True,
        "account_id": ACCOUNT_ID,
        "project_id": PROJECT_ID,
        "business_connection_id": CONNECTION_ID,
        "provider_dialog_id": DIALOG_ID,
        "provider_actor_id": ACTOR_ID,
    }
    values.update(overrides)
    return ApprovedPrivateBinding(**values)


class Bindings:
    def __init__(self, approved: ApprovedPrivateBinding | None):
        self.approved = approved
        self.calls: list[dict[str, str]] = []

    async def resolve(self, **scope):
        self.calls.append(scope)
        return self.approved


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


def command(**overrides) -> ProcessUniversalMayakPrivateMessageCommand:
    values = {
        "account_id": ACCOUNT_ID,
        "project_id": PROJECT_ID,
        "canonical_message_id": MESSAGE_ID,
        "business_connection_id": CONNECTION_ID,
        "provider_dialog_id": DIALOG_ID,
        "provider_actor_id": ACTOR_ID,
        "direction": CanonicalMessageDirection.INBOUND,
        "current_route": WorkRoute.HUMAN_REPLY_TASK,
        "text": "/start",
        "runtime_enabled": True,
        "connector_enabled": True,
        "credentials_ready": True,
        "now": NOW,
    }
    values.update(overrides)
    return ProcessUniversalMayakPrivateMessageCommand(**values)


@pytest.mark.anyio
async def test_default_off_blocks_before_binding_resolution() -> None:
    bindings = Bindings(binding())
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateMessageService(
        bindings=bindings,
        mayak=mayak,
    )

    result = await service.execute(command(runtime_enabled=False))

    assert result.disposition is PrivateIngressDisposition.DISABLED
    assert result.suppress_human_reply_task is False
    assert bindings.calls == []
    assert mayak.calls == []


@pytest.mark.anyio
async def test_canonical_business_text_maps_to_one_opaque_command() -> None:
    bindings = Bindings(binding())
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateMessageService(
        bindings=bindings,
        mayak=mayak,
    )

    result = await service.execute(command())

    assert result.disposition is PrivateIngressDisposition.PROCESSED
    assert result.suppress_human_reply_task is True
    assert bindings.calls == [
        {
            "account_id": ACCOUNT_ID,
            "project_id": PROJECT_ID,
            "business_connection_id": CONNECTION_ID,
            "provider_dialog_id": DIALOG_ID,
            "provider_actor_id": ACTOR_ID,
        }
    ]
    assert len(mayak.calls) == 1
    mapped = mayak.calls[0]
    assert mapped.scope == SCOPE
    assert mapped.event_ref == f"canonical_message_{MESSAGE_ID}"
    assert mapped.idempotency_key == f"mayak_message_{MESSAGE_ID}"
    assert mapped.invocation.value == "start"
    assert mapped.actor_scope == "owner"
    assert DIALOG_ID not in repr(mapped)
    assert ACTOR_ID not in repr(mapped)
    assert CONNECTION_ID not in repr(mapped)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("direction", "route"),
    [
        (CanonicalMessageDirection.OUTBOUND, WorkRoute.HUMAN_REPLY_TASK),
        (CanonicalMessageDirection.UNKNOWN, WorkRoute.HUMAN_REPLY_TASK),
        (CanonicalMessageDirection.INBOUND, WorkRoute.IGNORED),
        (CanonicalMessageDirection.INBOUND, WorkRoute.AGENT_TURN),
    ],
)
async def test_non_inbound_or_non_human_candidate_fails_before_binding(
    direction,
    route,
) -> None:
    bindings = Bindings(binding())
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateMessageService(
        bindings=bindings,
        mayak=mayak,
    )

    result = await service.execute(command(direction=direction, current_route=route))

    assert result.disposition is PrivateIngressDisposition.BLOCKED
    assert result.code == "mayak_private_route_not_eligible"
    assert bindings.calls == []
    assert mayak.calls == []


@pytest.mark.anyio
async def test_unapproved_binding_never_reaches_model_path() -> None:
    bindings = Bindings(None)
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateMessageService(
        bindings=bindings,
        mayak=mayak,
    )

    result = await service.execute(command())

    assert result.disposition is PrivateIngressDisposition.BLOCKED
    assert result.code == "mayak_private_binding_not_active"
    assert mayak.calls == []


@pytest.mark.anyio
async def test_binding_scope_mismatch_fails_closed() -> None:
    bindings = Bindings(binding(provider_dialog_id="different_dialog"))
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateMessageService(
        bindings=bindings,
        mayak=mayak,
    )

    with pytest.raises(
        UniversalMayakPrivateIngressError,
        match="mayak_private_binding_scope_mismatch",
    ):
        await service.execute(command())

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
    bindings = Bindings(binding())
    mayak = Mayak()
    service = ProcessUniversalMayakPrivateMessageService(
        bindings=bindings,
        mayak=mayak,
    )

    await service.execute(command(text=text))

    assert mayak.calls[0].invocation.value == expected


def test_canonical_uuid_and_time_are_mandatory() -> None:
    with pytest.raises(ValueError, match="canonical_message_id"):
        command(canonical_message_id="telegram-update-10")
    with pytest.raises(ValueError, match="timezone-aware"):
        command(now=datetime(2026, 7, 25, 8, 0))
