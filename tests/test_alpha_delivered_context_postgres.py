"""PostgreSQL proof for literal Alpha delivery continuity and isolation."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.alpha_shadow_worker import (
    AlphaShadowWorkerError,
    ProcessAlphaShadowMessageCommand,
)
from app.core.config import get_settings
from app.infrastructure.alpha_shadow_worker import (
    SessionFactoryAlphaShadowTurnRepository,
    _load_context,
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
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace
from app.models.domain import Base, TelegramReceipt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _payload(
    *,
    update_id: int,
    message_id: int,
    chat_id: int,
    sender_id: int,
    text_value: str,
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1_784_086_400 + message_id,
            "chat": {"id": chat_id, "type": "supergroup"},
            "from": {"id": sender_id, "is_bot": False},
            "text": text_value,
        },
    }


def _turn(
    *,
    workspace_id: UUID,
    session_id: UUID,
    trigger_message_id: UUID,
    revision_id: UUID,
    candidate_text: str,
    candidate_sha: str,
) -> AgentTurnModel:
    return AgentTurnModel(
        id=uuid4(),
        workspace_id=workspace_id,
        session_id=session_id,
        trigger_message_id=trigger_message_id,
        instruction_revision_id=revision_id,
        context_message_ids=[],
        status="proposed",
        result_kind="reply",
        result_code="fixture_reply",
        candidate_text=candidate_text,
        candidate_sha256=candidate_sha,
    )


@pytest.mark.anyio
async def test_sent_reply_survives_revision_upgrade_once_and_bad_scopes_stay_out() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Alpha context PostgreSQL test")

    schema = f"alpha_context_{uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        base_time = datetime(2026, 7, 16, 10, tzinfo=UTC)
        workspace_id = uuid4()
        connector_id = uuid4()
        dialog_id = uuid4()
        other_dialog_id = uuid4()
        definition_id = uuid4()
        current_revision_id = uuid4()
        historical_revision_id = uuid4()
        current_deployment_id = uuid4()
        historical_deployment_id = uuid4()
        installation_id = uuid4()
        source_receipt_id = uuid4()
        source_message_id = uuid4()
        other_receipt_id = uuid4()
        other_message_id = uuid4()
        trigger_receipt_id = uuid4()
        trigger_message_id = uuid4()
        wrong_trigger_receipt_id = uuid4()
        wrong_trigger_message_id = uuid4()
        wrong_source_receipt_id = uuid4()
        wrong_source_message_id = uuid4()
        historical_candidate = "Previously delivered Alpha reply"
        historical_sha = hashlib.sha256(historical_candidate.encode()).hexdigest()
        second_candidate = "Another delivered Alpha reply"
        second_sha = hashlib.sha256(second_candidate.encode()).hexdigest()
        failed_sha = hashlib.sha256(b"Failed proposal").hexdigest()
        unknown_sha = hashlib.sha256(b"Unknown proposal").hexdigest()
        blocked_sha = hashlib.sha256(b"Blocked proposal").hexdigest()
        revoked_sha = hashlib.sha256(b"Revoked proposal").hexdigest()
        cross_dialog_sha = hashlib.sha256(b"Cross-dialog proposal").hexdigest()
        proposed_only_sha = hashlib.sha256(b"Proposed only").hexdigest()
        wrong_connector_sha = hashlib.sha256(b"Wrong connector proposal").hexdigest()
        mismatched_sha = "f" * 64

        current_dialog = Dialog(
            id=dialog_id,
            workspace_id=workspace_id,
            connector_id=connector_id,
            provider_dialog_id="-100730000001",
            dialog_kind="supergroup",
        )
        other_dialog = Dialog(
            id=other_dialog_id,
            workspace_id=workspace_id,
            connector_id=connector_id,
            provider_dialog_id="-100730000002",
            dialog_kind="supergroup",
        )
        source_receipt = TelegramReceipt(
            id=source_receipt_id,
            bot_key="alpha_group",
            update_id=1,
            payload=_payload(
                update_id=1,
                message_id=1,
                chat_id=-100730000001,
                sender_id=740000001,
                text_value="/alpha@AlphaViaBot earlier",
            ),
            received_at=base_time + timedelta(seconds=1),
        )
        source_message = CanonicalMessage(
            id=source_message_id,
            workspace_id=workspace_id,
            dialog_id=dialog_id,
            receipt_id=source_receipt_id,
            provider_message_id="1",
            provider_sender_id="740000001",
            direction="inbound",
            occurred_at=base_time + timedelta(seconds=1),
        )
        other_receipt = TelegramReceipt(
            id=other_receipt_id,
            bot_key="alpha_group",
            update_id=2,
            payload=_payload(
                update_id=2,
                message_id=2,
                chat_id=-100730000002,
                sender_id=740000002,
                text_value="/alpha@AlphaViaBot other group",
            ),
            received_at=base_time + timedelta(seconds=2),
        )
        other_message = CanonicalMessage(
            id=other_message_id,
            workspace_id=workspace_id,
            dialog_id=other_dialog_id,
            receipt_id=other_receipt_id,
            provider_message_id="2",
            provider_sender_id="740000002",
            direction="inbound",
            occurred_at=base_time + timedelta(seconds=2),
        )
        trigger_receipt = TelegramReceipt(
            id=trigger_receipt_id,
            bot_key="alpha_group",
            update_id=3,
            payload=_payload(
                update_id=3,
                message_id=3,
                chat_id=-100730000001,
                sender_id=740000003,
                text_value="/alpha@AlphaViaBot continue",
            ),
            received_at=base_time + timedelta(seconds=5),
        )
        trigger_message = CanonicalMessage(
            id=trigger_message_id,
            workspace_id=workspace_id,
            dialog_id=dialog_id,
            receipt_id=trigger_receipt_id,
            provider_message_id="3",
            provider_sender_id="740000003",
            direction="inbound",
            occurred_at=base_time + timedelta(seconds=5),
        )
        wrong_trigger_receipt = TelegramReceipt(
            id=wrong_trigger_receipt_id,
            bot_key="not_alpha_group",
            update_id=4,
            payload=_payload(
                update_id=4,
                message_id=4,
                chat_id=-100730000001,
                sender_id=740000004,
                text_value="/alpha@AlphaViaBot wrong connector",
            ),
            received_at=base_time + timedelta(seconds=6),
        )
        wrong_trigger_message = CanonicalMessage(
            id=wrong_trigger_message_id,
            workspace_id=workspace_id,
            dialog_id=dialog_id,
            receipt_id=wrong_trigger_receipt_id,
            provider_message_id="4",
            provider_sender_id="740000004",
            direction="inbound",
            occurred_at=base_time + timedelta(seconds=6),
        )
        wrong_source_receipt = TelegramReceipt(
            id=wrong_source_receipt_id,
            bot_key="not_alpha_group",
            update_id=5,
            payload=_payload(
                update_id=5,
                message_id=5,
                chat_id=-100730000001,
                sender_id=740000005,
                text_value="/alpha@AlphaViaBot wrong connector context",
            ),
            received_at=base_time + timedelta(seconds=2, milliseconds=500),
        )
        wrong_source_message = CanonicalMessage(
            id=wrong_source_message_id,
            workspace_id=workspace_id,
            dialog_id=dialog_id,
            receipt_id=wrong_source_receipt_id,
            provider_message_id="5",
            provider_sender_id="740000005",
            direction="inbound",
            occurred_at=base_time + timedelta(seconds=2, milliseconds=500),
        )

        session_specs = {
            "historical": (dialog_id, historical_deployment_id, historical_revision_id),
            "second": (dialog_id, current_deployment_id, current_revision_id),
            "proposed": (dialog_id, current_deployment_id, current_revision_id),
            "failed": (dialog_id, current_deployment_id, current_revision_id),
            "unknown": (dialog_id, current_deployment_id, current_revision_id),
            "blocked": (dialog_id, current_deployment_id, current_revision_id),
            "revoked": (dialog_id, current_deployment_id, current_revision_id),
            "mismatched": (dialog_id, current_deployment_id, current_revision_id),
            "wrong_connector": (
                dialog_id,
                current_deployment_id,
                current_revision_id,
            ),
            "cross_dialog": (
                other_dialog_id,
                current_deployment_id,
                current_revision_id,
            ),
        }
        session_specs.update(
            {
                f"duplicate_{index}": (
                    dialog_id,
                    historical_deployment_id,
                    historical_revision_id,
                )
                for index in range(70)
            }
        )
        session_ids = {key: uuid4() for key in session_specs}
        agent_sessions = [
            AgentSessionModel(
                id=session_ids[key],
                workspace_id=workspace_id,
                dialog_id=scope[0],
                deployment_id=scope[1],
                instruction_revision_id=scope[2],
                session_key=f"context-{key}",
                state="active",
            )
            for key, scope in session_specs.items()
        ]
        turns = [
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["historical"],
                trigger_message_id=source_message_id,
                revision_id=historical_revision_id,
                candidate_text=historical_candidate,
                candidate_sha=historical_sha,
            ),
            *[
                _turn(
                    workspace_id=workspace_id,
                    session_id=session_ids[f"duplicate_{index}"],
                    trigger_message_id=source_message_id,
                    revision_id=historical_revision_id,
                    candidate_text=historical_candidate,
                    candidate_sha=historical_sha,
                )
                for index in range(70)
            ],
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["second"],
                trigger_message_id=source_message_id,
                revision_id=current_revision_id,
                candidate_text=second_candidate,
                candidate_sha=second_sha,
            ),
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["proposed"],
                trigger_message_id=source_message_id,
                revision_id=current_revision_id,
                candidate_text="Proposed only",
                candidate_sha=proposed_only_sha,
            ),
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["failed"],
                trigger_message_id=source_message_id,
                revision_id=current_revision_id,
                candidate_text="Failed proposal",
                candidate_sha=failed_sha,
            ),
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["unknown"],
                trigger_message_id=source_message_id,
                revision_id=current_revision_id,
                candidate_text="Unknown proposal",
                candidate_sha=unknown_sha,
            ),
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["blocked"],
                trigger_message_id=source_message_id,
                revision_id=current_revision_id,
                candidate_text="Blocked proposal",
                candidate_sha=blocked_sha,
            ),
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["revoked"],
                trigger_message_id=source_message_id,
                revision_id=current_revision_id,
                candidate_text="Revoked proposal",
                candidate_sha=revoked_sha,
            ),
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["mismatched"],
                trigger_message_id=source_message_id,
                revision_id=current_revision_id,
                candidate_text="Hash mismatch",
                candidate_sha=mismatched_sha,
            ),
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["cross_dialog"],
                trigger_message_id=other_message_id,
                revision_id=current_revision_id,
                candidate_text="Cross-dialog proposal",
                candidate_sha=cross_dialog_sha,
            ),
            _turn(
                workspace_id=workspace_id,
                session_id=session_ids["wrong_connector"],
                trigger_message_id=wrong_source_message_id,
                revision_id=current_revision_id,
                candidate_text="Wrong connector proposal",
                candidate_sha=wrong_connector_sha,
            ),
        ]

        delivery_specs = (
            (source_message_id, historical_sha, "sent", "9001", False, 4),
            (source_message_id, second_sha, "sent", "9002", False, 3),
            (source_message_id, failed_sha, "failed", None, False, 2),
            (source_message_id, unknown_sha, "unknown", None, False, 2),
            (source_message_id, blocked_sha, "blocked", None, False, 2),
            (source_message_id, revoked_sha, "sent", "9003", True, 2),
            (source_message_id, mismatched_sha, "sent", "9004", False, 2),
            (other_message_id, cross_dialog_sha, "sent", "9005", False, 2),
            (
                wrong_source_message_id,
                wrong_connector_sha,
                "sent",
                "9006",
                False,
                3,
            ),
        )
        grants: list[DeliveryGrantModel] = []
        attempts: list[AgentSendAttemptModel] = []
        historical_attempt_id: UUID | None = None
        second_attempt_id: UUID | None = None
        for (
            source_id,
            candidate_sha,
            state,
            provider_id,
            revoked,
            delivered_second,
        ) in delivery_specs:
            grant_id = uuid4()
            attempt_id = uuid4()
            if candidate_sha == historical_sha:
                historical_attempt_id = attempt_id
            if candidate_sha == second_sha:
                second_attempt_id = attempt_id
            delivered_at = base_time + timedelta(seconds=delivered_second)
            grants.append(
                DeliveryGrantModel(
                    id=grant_id,
                    workspace_id=workspace_id,
                    installation_id=installation_id,
                    trigger_message_id=source_id,
                    grant_key=f"context-grant-{grant_id.hex}",
                    candidate_sha256=candidate_sha,
                    expires_at=base_time + timedelta(minutes=10),
                    used_at=delivered_at,
                    revoked_at=(delivered_at if revoked else None),
                )
            )
            attempts.append(
                AgentSendAttemptModel(
                    id=attempt_id,
                    workspace_id=workspace_id,
                    grant_id=grant_id,
                    state=state,
                    provider_message_id=provider_id,
                    error_code=None if state == "sent" else f"fixture_{state}",
                    created_at=delivered_at,
                    updated_at=delivered_at,
                )
            )
        assert historical_attempt_id is not None
        assert second_attempt_id is not None

        async with sessions.begin() as seed:
            seed.add(
                Workspace(
                    id=workspace_id,
                    key="mark-personal",
                    display_name="Mark personal",
                    is_active=True,
                )
            )
            await seed.flush()
            seed.add(
                TransportConnector(
                    id=connector_id,
                    workspace_id=workspace_id,
                    connector_key="alpha_group",
                    connector_kind="telegram_bot",
                    is_enabled=True,
                )
            )
            await seed.flush()
            seed.add_all(
                [
                    current_dialog,
                    other_dialog,
                    AgentDefinitionModel(
                        id=definition_id,
                        workspace_id=workspace_id,
                        agent_key="alpha_group",
                        display_name="Alpha",
                    ),
                ]
            )
            await seed.flush()
            seed.add_all(
                [
                    InstructionRevisionModel(
                        id=current_revision_id,
                        workspace_id=workspace_id,
                        agent_definition_id=definition_id,
                        revision_key="alpha-current",
                        source_repository="markmalina/alpha",
                        source_path="alpha/current",
                        source_commit_sha="a" * 40,
                        content_sha256="a" * 64,
                        schema_version="agent-bundle-v1",
                        validation_state="validated",
                    ),
                    InstructionRevisionModel(
                        id=historical_revision_id,
                        workspace_id=workspace_id,
                        agent_definition_id=definition_id,
                        revision_key="alpha-historical",
                        source_repository="markmalina/alpha",
                        source_path="alpha/historical",
                        source_commit_sha="b" * 40,
                        content_sha256="b" * 64,
                        schema_version="agent-bundle-v1",
                        validation_state="validated",
                    ),
                ]
            )
            await seed.flush()
            seed.add_all(
                [
                    AgentDeploymentModel(
                        id=current_deployment_id,
                        workspace_id=workspace_id,
                        agent_definition_id=definition_id,
                        instruction_revision_id=current_revision_id,
                        deployment_key="alpha-current",
                        runtime_profile="fixture",
                        model_profile="none",
                        policy_version="alpha-shadow-v2",
                        state="active",
                    ),
                    AgentDeploymentModel(
                        id=historical_deployment_id,
                        workspace_id=workspace_id,
                        agent_definition_id=definition_id,
                        instruction_revision_id=historical_revision_id,
                        deployment_key="alpha-historical",
                        runtime_profile="fixture",
                        model_profile="none",
                        policy_version="alpha-shadow-v1",
                        state="paused",
                    ),
                ]
            )
            await seed.flush()
            seed.add(
                AgentInstallationModel(
                    id=installation_id,
                    workspace_id=workspace_id,
                    connector_id=connector_id,
                    dialog_id=dialog_id,
                    deployment_id=current_deployment_id,
                    instruction_revision_id=current_revision_id,
                    installation_key="installation-alpha",
                    dialog_label="Alpha Group",
                    state="active",
                    max_turns_per_window=5,
                    rate_window_seconds=60,
                    max_tokens_per_turn=2048,
                )
            )
            seed.add_all(
                [
                    source_receipt,
                    other_receipt,
                    trigger_receipt,
                    wrong_trigger_receipt,
                    wrong_source_receipt,
                ]
            )
            await seed.flush()
            seed.add_all(
                [
                    source_message,
                    other_message,
                    trigger_message,
                    wrong_trigger_message,
                    wrong_source_message,
                ]
            )
            await seed.flush()
            seed.add_all(agent_sessions)
            await seed.flush()
            seed.add_all(turns)
            await seed.flush()
            seed.add_all(grants)
            await seed.flush()
            seed.add_all(attempts)

        async with sessions() as verification:
            first = await _load_context(
                verification,
                workspace_id=workspace_id,
                dialog=current_dialog,
                trigger=trigger_message,
                trigger_received_at=trigger_receipt.received_at,
                installation_id=installation_id,
                connector_key="alpha_group",
                bot_username="AlphaViaBot",
            )
            replay = await _load_context(
                verification,
                workspace_id=workspace_id,
                dialog=current_dialog,
                trigger=trigger_message,
                trigger_received_at=trigger_receipt.received_at,
                installation_id=installation_id,
                connector_key="alpha_group",
                bot_username="AlphaViaBot",
            )

        assert tuple(item.message_id for item in first) == (
            str(source_message_id),
            f"alpha-send-{second_attempt_id}",
            f"alpha-send-{historical_attempt_id}",
        )
        assert tuple(item.role for item in first) == ("user", "assistant", "assistant")
        assert tuple(item.content for item in first) == (
            "/alpha@AlphaViaBot earlier",
            second_candidate,
            historical_candidate,
        )
        assert replay == first

        repository = SessionFactoryAlphaShadowTurnRepository(sessions)
        with pytest.raises(AlphaShadowWorkerError, match="alpha_trigger_connector_mismatch"):
            await repository.prepare(
                ProcessAlphaShadowMessageCommand(
                    workspace_key="mark-personal",
                    trigger_message_id=str(wrong_trigger_message_id),
                    bot_username="AlphaViaBot",
                    now=base_time + timedelta(seconds=7),
                )
            )
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
