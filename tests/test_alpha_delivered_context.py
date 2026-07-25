"""Unit proof that only literally delivered Alpha output enters later context."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.alpha_shadow_worker import AlphaShadowWorkerError
from app.infrastructure.alpha_shadow_worker import (
    _load_context,
    _require_trigger_connector,
)
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    AgentSessionModel,
    AgentTurnModel,
    InstructionRevisionModel,
)
from app.models.alpha import (
    AgentInstallationModel,
    AgentSendAttemptModel,
    DeliveryGrantModel,
)
from app.models.canonical import CanonicalMessage, Dialog
from app.models.domain import TelegramReceipt


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _Session:
    def __init__(self, *results: list[tuple[object, ...]]) -> None:
        self._results = list(results)
        self.statements: list[str] = []

    async def execute(self, _statement: object) -> _Rows:
        self.statements.append(str(_statement))
        return _Rows(self._results.pop(0))


def _payload(*, chat_id: int, message_id: int, sender_id: int, text: str) -> dict[str, object]:
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "date": message_id,
            "chat": {"id": chat_id, "type": "supergroup"},
            "from": {"id": sender_id, "is_bot": False},
            "text": text,
        },
    }


def _delivered_row(
    *,
    workspace_id: UUID,
    dialog_id: UUID,
    installation_id: UUID,
    deployment_id: UUID,
    revision_id: UUID,
    trigger_id: UUID,
    trigger_receipt_id: UUID,
    delivered_at: datetime,
    state: str = "sent",
    candidate_text: str = "Delivered reply",
    candidate_sha: str | None = None,
    attempt_id: UUID | None = None,
    installation_deployment_id: UUID | None = None,
    installation_revision_id: UUID | None = None,
    revoked: bool = False,
    receipt_bot_key: str = "alpha_group",
) -> tuple[object, ...]:
    candidate_sha = candidate_sha or hashlib.sha256(candidate_text.encode()).hexdigest()
    grant_id = uuid4()
    session_id = uuid4()
    attempt = AgentSendAttemptModel(
        id=attempt_id or uuid4(),
        workspace_id=workspace_id,
        grant_id=grant_id,
        state=state,
        provider_message_id="901" if state == "sent" else None,
        error_code=None if state == "sent" else f"fixture_{state}",
        created_at=delivered_at,
        updated_at=delivered_at,
    )
    grant = DeliveryGrantModel(
        id=grant_id,
        workspace_id=workspace_id,
        installation_id=installation_id,
        trigger_message_id=trigger_id,
        grant_key=f"grant-{grant_id.hex}",
        candidate_sha256=candidate_sha,
        expires_at=delivered_at + timedelta(minutes=5),
        used_at=delivered_at,
        revoked_at=delivered_at if revoked else None,
    )
    installation = AgentInstallationModel(
        id=installation_id,
        workspace_id=workspace_id,
        connector_id=uuid4(),
        dialog_id=dialog_id,
        deployment_id=installation_deployment_id or deployment_id,
        instruction_revision_id=installation_revision_id or revision_id,
        installation_key=f"installation-{installation_id.hex}",
        dialog_label="Alpha Group",
        state="active",
        max_turns_per_window=5,
        rate_window_seconds=60,
        max_tokens_per_turn=2048,
    )
    session = AgentSessionModel(
        id=session_id,
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        deployment_id=deployment_id,
        instruction_revision_id=revision_id,
        session_key=f"session-{session_id.hex}",
        state="active",
    )
    turn = AgentTurnModel(
        id=uuid4(),
        workspace_id=workspace_id,
        session_id=session_id,
        trigger_message_id=trigger_id,
        instruction_revision_id=revision_id,
        context_message_ids=[],
        status="proposed",
        result_kind="reply",
        result_code="fixture_reply",
        candidate_text=candidate_text,
        candidate_sha256=candidate_sha,
    )
    definition_id = uuid4()
    deployment = AgentDeploymentModel(
        id=deployment_id,
        workspace_id=workspace_id,
        agent_definition_id=definition_id,
        instruction_revision_id=revision_id,
        deployment_key=f"deployment-{deployment_id.hex}",
        runtime_profile="fixture",
        model_profile="none",
        policy_version="alpha-shadow-v1",
        state="paused",
    )
    revision = InstructionRevisionModel(
        id=revision_id,
        workspace_id=workspace_id,
        agent_definition_id=definition_id,
        revision_key=f"revision-{revision_id.hex}",
        source_repository="markmalina/alpha",
        source_path="alpha/v1",
        source_commit_sha="a" * 40,
        content_sha256="b" * 64,
        schema_version="agent-bundle-v1",
        validation_state="validated",
    )
    definition = AgentDefinitionModel(
        id=definition_id,
        workspace_id=workspace_id,
        agent_key="alpha_group",
        display_name="Alpha",
    )
    source_trigger = CanonicalMessage(
        id=trigger_id,
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        receipt_id=trigger_receipt_id,
        provider_message_id="10",
        provider_sender_id="7001",
        direction="inbound",
        occurred_at=delivered_at - timedelta(seconds=1),
    )
    source_receipt = TelegramReceipt(
        id=trigger_receipt_id,
        bot_key=receipt_bot_key,
        update_id=10,
        payload=_payload(
            chat_id=-100700000001,
            message_id=10,
            sender_id=7001,
            text="/alpha@AlphaViaBot first",
        ),
        received_at=delivered_at - timedelta(seconds=1),
    )
    return (
        attempt,
        grant,
        installation,
        session,
        turn,
        deployment,
        revision,
        definition,
        source_trigger,
        source_receipt,
    )


@pytest.mark.anyio
async def test_context_merges_verified_inbound_and_exactly_one_sent_candidate() -> None:
    base = datetime(2026, 7, 16, 10, tzinfo=UTC)
    workspace_id = uuid4()
    dialog_id = uuid4()
    installation_id = uuid4()
    deployment_id = uuid4()
    revision_id = uuid4()
    source_trigger_id = uuid4()
    source_receipt_id = uuid4()
    current_trigger_id = uuid4()
    dialog = Dialog(
        id=dialog_id,
        workspace_id=workspace_id,
        connector_id=uuid4(),
        provider_dialog_id="-100700000001",
        dialog_kind="supergroup",
    )
    current_trigger = CanonicalMessage(
        id=current_trigger_id,
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        receipt_id=uuid4(),
        provider_message_id="20",
        provider_sender_id="7002",
        direction="inbound",
        occurred_at=base + timedelta(seconds=4),
    )
    inbound_id = uuid4()
    inbound_receipt_id = uuid4()
    inbound = CanonicalMessage(
        id=inbound_id,
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        receipt_id=inbound_receipt_id,
        provider_message_id="11",
        provider_sender_id="7003",
        direction="inbound",
        occurred_at=base + timedelta(seconds=3),
    )
    inbound_receipt = TelegramReceipt(
        id=inbound_receipt_id,
        bot_key="alpha_group",
        update_id=11,
        payload=_payload(
            chat_id=-100700000001,
            message_id=11,
            sender_id=7003,
            text="/alpha@AlphaViaBot Second user message",
        ),
        received_at=base + timedelta(seconds=3),
    )
    ordinary_inbound_id = uuid4()
    ordinary_receipt_id = uuid4()
    ordinary_inbound = CanonicalMessage(
        id=ordinary_inbound_id,
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        receipt_id=ordinary_receipt_id,
        provider_message_id="13",
        provider_sender_id="7005",
        direction="inbound",
        occurred_at=base + timedelta(seconds=3),
    )
    ordinary_receipt = TelegramReceipt(
        id=ordinary_receipt_id,
        bot_key="alpha_group",
        update_id=13,
        payload=_payload(
            chat_id=-100700000001,
            message_id=13,
            sender_id=7005,
            text="Private ordinary group text",
        ),
        received_at=base + timedelta(seconds=3),
    )
    wrong_inbound_id = uuid4()
    wrong_inbound_receipt_id = uuid4()
    wrong_inbound = CanonicalMessage(
        id=wrong_inbound_id,
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        receipt_id=wrong_inbound_receipt_id,
        provider_message_id="12",
        provider_sender_id="7004",
        direction="inbound",
        occurred_at=base + timedelta(seconds=3),
    )
    wrong_inbound_receipt = TelegramReceipt(
        id=wrong_inbound_receipt_id,
        bot_key="not_alpha_group",
        update_id=12,
        payload=_payload(
            chat_id=-100700000001,
            message_id=12,
            sender_id=7004,
            text="Wrong connector inbound",
        ),
        received_at=base + timedelta(seconds=3),
    )

    delivered_attempt_id = uuid4()
    valid = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        attempt_id=delivered_attempt_id,
    )
    historical_attempt_id = uuid4()
    historical = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=uuid4(),
        revision_id=uuid4(),
        installation_deployment_id=deployment_id,
        installation_revision_id=revision_id,
        trigger_id=uuid4(),
        trigger_receipt_id=uuid4(),
        delivered_at=base + timedelta(seconds=1, milliseconds=500),
        attempt_id=historical_attempt_id,
        candidate_text="Reply from the previous validated revision",
    )
    failed = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        state="failed",
        candidate_text="Failed reply",
    )
    unknown = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        state="unknown",
        candidate_text="Unknown delivery",
    )
    blocked = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        state="blocked",
        candidate_text="Blocked delivery",
    )
    wrong_workspace = _delivered_row(
        workspace_id=uuid4(),
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        candidate_text="Other workspace",
    )
    bad_hash = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        candidate_text="Tampered reply",
        candidate_sha="f" * 64,
    )
    revoked = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        candidate_text="Revoked delivery",
        revoked=True,
    )
    wrong_connector = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=dialog_id,
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        candidate_text="Other connector",
        receipt_bot_key="not_alpha_group",
    )
    wrong_dialog = _delivered_row(
        workspace_id=workspace_id,
        dialog_id=uuid4(),
        installation_id=installation_id,
        deployment_id=deployment_id,
        revision_id=revision_id,
        trigger_id=source_trigger_id,
        trigger_receipt_id=source_receipt_id,
        delivered_at=base + timedelta(seconds=2),
        candidate_text="Other dialog",
    )
    session = _Session(
        [
            (inbound, inbound_receipt),
            (ordinary_inbound, ordinary_receipt),
            (wrong_inbound, wrong_inbound_receipt),
        ],
        [
            *([valid] * 70),
            historical,
            failed,
            unknown,
            blocked,
            wrong_workspace,
            wrong_dialog,
            bad_hash,
            revoked,
            wrong_connector,
        ],
    )

    context = await _load_context(  # type: ignore[arg-type]
        session,
        workspace_id=workspace_id,
        dialog=dialog,
        trigger=current_trigger,
        trigger_received_at=base + timedelta(seconds=4),
        installation_id=installation_id,
        connector_key="alpha_group",
        bot_username="AlphaViaBot",
    )

    assert tuple(item.message_id for item in context) == (
        f"alpha-send-{historical_attempt_id}",
        f"alpha-send-{delivered_attempt_id}",
        str(inbound_id),
    )
    assert tuple(item.role for item in context) == ("assistant", "assistant", "user")
    assert tuple(item.content for item in context) == (
        "Reply from the previous validated revision",
        "Delivered reply",
        "/alpha@AlphaViaBot Second user message",
    )
    delivered_sql = session.statements[1]
    assert "recent_alpha_sent_attempts" in delivered_sql
    assert delivered_sql.count("LIMIT") == 1
    assert delivered_sql.index("LIMIT") < delivered_sql.index("JOIN agent_sessions")


def test_trigger_receipt_must_match_connector_key() -> None:
    receipt = TelegramReceipt(
        id=uuid4(),
        bot_key="alpha_group",
        update_id=1,
        payload={},
        received_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

    _require_trigger_connector(receipt=receipt, connector_key="alpha_group")
    with pytest.raises(AlphaShadowWorkerError, match="alpha_trigger_connector_mismatch"):
        _require_trigger_connector(receipt=receipt, connector_key="other_connector")
