"""Safety and orchestration tests for the one-group Alpha canary."""

import asyncio
from datetime import UTC, datetime

import pytest
from app.application.agent_kernel import AgentResultKind
from app.application.alpha_canary import (
    ALPHA_DISCLOSURE_PREFIX,
    AlphaCanaryDisposition,
    AlphaCanaryError,
    AlphaGrantResult,
    AlphaMembershipAction,
    AlphaMembershipResult,
    ProcessAlphaCanaryUpdateCommand,
    ProcessAlphaCanaryUpdateService,
)
from app.application.alpha_delivery import AlphaDeliveryResult
from app.application.alpha_group import (
    AgentSendAttemptState,
    AlphaInvocationKind,
)
from app.application.alpha_shadow_worker import (
    AlphaProposalDeliveryMode,
    AlphaShadowDisposition,
    AlphaShadowWorkerResult,
    recognize_alpha_invocation,
)

NOW = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)


class RecordingStore:
    def __init__(self) -> None:
        self.memberships = []
        self.grants = []
        self.active_group = True

    async def is_active_group(self, **scope):
        return self.active_group

    async def reconcile_membership(self, command):
        self.memberships.append(command)
        return AlphaMembershipResult(action=command.action, installation_key="install-1")

    async def issue_delivery_grant(self, command):
        self.grants.append(command)
        return AlphaGrantResult(grant_key="grant-1")


class FixedShadow:
    def __init__(self, result: AlphaShadowWorkerResult) -> None:
        self.result = result
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        return self.result


class RecordingDelivery:
    def __init__(self) -> None:
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        return AlphaDeliveryResult(
            attempt_key="attempt-1",
            state=AgentSendAttemptState.SENT,
            sent=True,
            idempotent_replay=False,
            provider_message_id="7001",
        )


def _message_update(chat_id: str = "-1007001") -> dict:
    return {
        "update_id": 41,
        "message": {
            "message_id": 7,
            "chat": {"id": chat_id, "type": "supergroup", "title": "Canary"},
            "from": {"id": 99, "is_bot": False},
            "text": "/alpha status",
        },
    }


def _command(**changes) -> ProcessAlphaCanaryUpdateCommand:
    values = {
        "workspace_key": "mark-personal",
        "update": _message_update(),
        "canonical_message_id": "00000000-0000-0000-0000-000000000041",
        "bot_username": "AlphaViaBot",
        "connector_enabled": True,
        "shadow_worker_enabled": True,
        "canary_enabled": True,
        "delivery_enabled": False,
        "privacy_mode_confirmed": True,
        "credentials_ready": True,
        "now": NOW,
    }
    values.update(changes)
    return ProcessAlphaCanaryUpdateCommand(**values)


def _service(shadow_result: AlphaShadowWorkerResult):
    store = RecordingStore()
    shadow = FixedShadow(shadow_result)
    delivery = RecordingDelivery()
    service = ProcessAlphaCanaryUpdateService(
        store=store,
        shadow=shadow,
        delivery=delivery,
    )
    return service, store, shadow, delivery


def test_privacy_mode_canary_command_targets_the_exact_bot() -> None:
    assert (
        recognize_alpha_invocation(
            "/alpha@AlphaViaBot Ответь одним сообщением: Альфа на связи.",
            bot_username="AlphaViaBot",
            reply_to_bot=False,
        )
        is AlphaInvocationKind.COMMAND
    )
    assert (
        recognize_alpha_invocation(
            "/alpha@OtherAlphaBot Ответь одним сообщением: Альфа на связи.",
            bot_username="AlphaViaBot",
            reply_to_bot=False,
        )
        is AlphaInvocationKind.NONE
    )


def test_disabled_canary_touches_nothing() -> None:
    service, store, shadow, delivery = _service(
        AlphaShadowWorkerResult(AlphaShadowDisposition.PROPOSED, "unused")
    )

    result = asyncio.run(service.execute(_command(canary_enabled=False)))

    assert result.disposition is AlphaCanaryDisposition.DISABLED
    assert result.code == "alpha_canary_disabled"
    assert store.memberships == []
    assert store.grants == []
    assert shadow.commands == []
    assert delivery.commands == []


def test_membership_discovery_requires_exact_bot_and_records_group() -> None:
    service, store, shadow, delivery = _service(
        AlphaShadowWorkerResult(AlphaShadowDisposition.PROPOSED, "unused")
    )
    update = {
        "update_id": 42,
        "my_chat_member": {
            "chat": {
                "id": "-1007001",
                "type": "supergroup",
                "title": "Canary",
            },
            "new_chat_member": {
                "status": "member",
                "user": {"id": 12, "is_bot": True, "username": "AlphaViaBot"},
            },
        },
    }

    result = asyncio.run(
        service.execute(_command(update=update, canonical_message_id=None))
    )

    assert result.disposition is AlphaCanaryDisposition.DISCOVERED
    assert store.memberships[0].action is AlphaMembershipAction.PRESENT
    assert shadow.commands == []
    assert delivery.commands == []


def test_unapproved_group_and_ambiguous_shapes_fail_before_runtime() -> None:
    service, store, shadow, delivery = _service(
        AlphaShadowWorkerResult(AlphaShadowDisposition.PROPOSED, "unused")
    )
    store.active_group = False
    outside = asyncio.run(service.execute(_command()))
    assert outside.code == "alpha_canary_group_not_active"
    store.active_group = True

    with pytest.raises(AlphaCanaryError, match="update_shape_invalid"):
        asyncio.run(
            service.execute(
                _command(
                    update={
                        **_message_update(),
                        "edited_message": {"chat": {"id": "-1007001"}},
                    }
                )
            )
        )
    assert store.grants == []
    assert shadow.commands == []
    assert delivery.commands == []


@pytest.mark.parametrize(
    "media_payload",
    [
        {"voice": {"file_id": "voice-1", "duration": 3}},
        {"photo": [{"file_id": "photo-1", "width": 90, "height": 90}]},
        {"sticker": {"file_id": "sticker-1", "emoji": "👋"}},
    ],
    ids=["voice", "photo", "sticker"],
)
def test_non_text_messages_are_terminally_ignored_before_shadow(media_payload) -> None:
    service, store, shadow, delivery = _service(
        AlphaShadowWorkerResult(AlphaShadowDisposition.PROPOSED, "unused")
    )
    update = _message_update()
    update["message"].pop("text")
    update["message"].update(media_payload)

    result = asyncio.run(
        service.execute(
            _command(
                update=update,
                canonical_message_id=None,
                delivery_enabled=True,
            )
        )
    )

    assert result.disposition is AlphaCanaryDisposition.NOT_INVOKED
    assert result.code == "alpha_canary_non_text_ignored"
    assert result.shadow is None
    assert result.delivery is None
    assert store.memberships == []
    assert store.grants == []
    assert shadow.commands == []
    assert delivery.commands == []


def test_proposal_stays_delivery_free_until_separate_kill_switch() -> None:
    proposed = AlphaShadowWorkerResult(
        AlphaShadowDisposition.PROPOSED,
        "fixture_reply",
        turn_key="turn-1",
        result_kind=AgentResultKind.REPLY,
        candidate_sha256="a" * 64,
        delivery_mode=AlphaProposalDeliveryMode.CANARY,
    )
    service, store, shadow, delivery = _service(proposed)

    result = asyncio.run(service.execute(_command(delivery_enabled=False)))

    assert result.disposition is AlphaCanaryDisposition.PROPOSED
    assert result.code == "alpha_canary_delivery_disabled"
    assert len(shadow.commands) == 1
    assert store.grants == []
    assert delivery.commands == []


def test_enabled_delivery_issues_exact_disclosed_grant_then_uses_opaque_key() -> None:
    proposed = AlphaShadowWorkerResult(
        AlphaShadowDisposition.REPLAYED,
        "fixture_reply",
        turn_key="turn-1",
        result_kind=AgentResultKind.REPLY,
        candidate_sha256="a" * 64,
        delivery_mode=AlphaProposalDeliveryMode.CANARY,
    )
    service, store, _shadow, delivery = _service(proposed)

    result = asyncio.run(service.execute(_command(delivery_enabled=True)))

    assert result.disposition is AlphaCanaryDisposition.SENT
    assert len(store.grants) == 1
    assert store.grants[0].provider_dialog_id == "-1007001"
    assert store.grants[0].disclosure_prefix == ALPHA_DISCLOSURE_PREFIX
    assert delivery.commands[0].grant_key == "grant-1"
    assert "-1007001" not in repr(delivery.commands[0])


def test_bounded_live_model_uses_existing_exact_grant_and_delivery_chain() -> None:
    proposed = AlphaShadowWorkerResult(
        AlphaShadowDisposition.REPLAYED,
        "alpha_answer",
        turn_key="turn-model-live-1",
        result_kind=AgentResultKind.REPLY,
        candidate_sha256="c" * 64,
        delivery_mode=AlphaProposalDeliveryMode.BOUNDED_LIVE,
    )
    service, store, _shadow, delivery = _service(proposed)

    result = asyncio.run(service.execute(_command(delivery_enabled=True)))

    assert result.disposition is AlphaCanaryDisposition.SENT
    assert len(store.grants) == 1
    assert store.grants[0].turn_key == "turn-model-live-1"
    assert store.grants[0].disclosure_prefix == ALPHA_DISCLOSURE_PREFIX
    assert len(delivery.commands) == 1


def test_model_shadow_cannot_issue_grant_even_when_delivery_switch_is_on() -> None:
    proposed = AlphaShadowWorkerResult(
        AlphaShadowDisposition.PROPOSED,
        "alpha_answer",
        turn_key="turn-model-1",
        result_kind=AgentResultKind.REPLY,
        candidate_sha256="b" * 64,
        delivery_mode=AlphaProposalDeliveryMode.SHADOW_ONLY,
    )
    service, store, _shadow, delivery = _service(proposed)

    result = asyncio.run(service.execute(_command(delivery_enabled=True)))

    assert result.disposition is AlphaCanaryDisposition.PROPOSED
    assert result.code == "alpha_model_shadow_delivery_blocked"
    assert store.grants == []
    assert delivery.commands == []
