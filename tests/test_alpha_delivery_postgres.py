"""PostgreSQL proof for exact-target Alpha grant consumption."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.application.alpha_delivery import (
    AlphaDeliveryError,
    DeliverAlphaGrantCommand,
    DeliverAlphaGrantService,
)
from app.application.alpha_group import AgentSendAttemptState
from app.core.config import get_settings
from app.infrastructure.alpha_delivery import SessionFactoryAlphaDeliveryStore
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
from app.models.domain import Base
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def send_text(self, *, chat_id: str, text: str) -> str:
        self.calls.append((chat_id, text))
        return "9001"


@pytest.mark.anyio
async def test_postgres_grant_binds_target_consumes_once_and_never_retries_unknown(
    tmp_path: Path,
) -> None:
    del tmp_path
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Alpha delivery PostgreSQL test")

    schema = f"alpha_delivery_{uuid4().hex}"
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
        now = datetime(2026, 7, 15, 12, tzinfo=UTC)
        workspace_id = uuid4()
        connector_id = uuid4()
        dialog_id = uuid4()
        definition_id = uuid4()
        revision_id = uuid4()
        deployment_id = uuid4()
        installation_id = uuid4()
        session_id = uuid4()
        trigger_ids = [uuid4(), uuid4(), uuid4()]
        grant_ids = [uuid4(), uuid4(), uuid4()]
        candidate = "Database-bound exact candidate"
        candidate_sha = hashlib.sha256(candidate.encode()).hexdigest()

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
                    Dialog(
                        id=dialog_id,
                        workspace_id=workspace_id,
                        connector_id=connector_id,
                        provider_dialog_id="-100730000001",
                        dialog_kind="supergroup",
                    ),
                    AgentDefinitionModel(
                        id=definition_id,
                        workspace_id=workspace_id,
                        agent_key="alpha_group",
                        display_name="Alpha",
                    ),
                ]
            )
            await seed.flush()
            seed.add(
                InstructionRevisionModel(
                    id=revision_id,
                    workspace_id=workspace_id,
                    agent_definition_id=definition_id,
                    revision_key="alpha-fixture-v1",
                    source_repository="markmalina/alpha",
                    source_path="alpha/v1",
                    source_commit_sha="a" * 40,
                    content_sha256="b" * 64,
                    schema_version="agent-bundle-v1",
                    validation_state="validated",
                )
            )
            await seed.flush()
            seed.add(
                AgentDeploymentModel(
                    id=deployment_id,
                    workspace_id=workspace_id,
                    agent_definition_id=definition_id,
                    instruction_revision_id=revision_id,
                    deployment_key="alpha-deployment-v1",
                    runtime_profile="fixture",
                    model_profile="none",
                    policy_version="alpha-shadow-v1",
                    state="active",
                )
            )
            await seed.flush()
            seed.add(
                AgentInstallationModel(
                    id=installation_id,
                    workspace_id=workspace_id,
                    connector_id=connector_id,
                    dialog_id=dialog_id,
                    deployment_id=deployment_id,
                    instruction_revision_id=revision_id,
                    installation_key="installation-alpha-canary",
                    dialog_label="Alpha canary",
                    state="active",
                    max_turns_per_window=5,
                    rate_window_seconds=60,
                    max_tokens_per_turn=128,
                )
            )
            seed.add_all(
                [
                    CanonicalMessage(
                        id=trigger_id,
                        workspace_id=workspace_id,
                        dialog_id=dialog_id,
                        provider_message_id=str(index + 1),
                        provider_sender_id=str(740000001 + index),
                        direction="inbound",
                        occurred_at=now + timedelta(seconds=index),
                    )
                    for index, trigger_id in enumerate(trigger_ids)
                ]
            )
            await seed.flush()
            seed.add(
                AgentSessionModel(
                    id=session_id,
                    workspace_id=workspace_id,
                    dialog_id=dialog_id,
                    deployment_id=deployment_id,
                    instruction_revision_id=revision_id,
                    session_key="alpha-session",
                    state="active",
                )
            )
            await seed.flush()
            seed.add_all(
                [
                    AgentTurnModel(
                        workspace_id=workspace_id,
                        session_id=session_id,
                        trigger_message_id=trigger_id,
                        instruction_revision_id=revision_id,
                        context_message_ids=[],
                        status="proposed",
                        result_kind="reply",
                        result_code="fixture_reply",
                        candidate_text=candidate,
                        candidate_sha256=candidate_sha,
                    )
                    for trigger_id in trigger_ids
                ]
            )
            seed.add_all(
                [
                    DeliveryGrantModel(
                        id=grant_id,
                        workspace_id=workspace_id,
                        installation_id=installation_id,
                        trigger_message_id=trigger_id,
                        grant_key=f"grant-{index + 1}",
                        candidate_sha256=candidate_sha,
                        expires_at=now + timedelta(minutes=5),
                        used_at=(now if index == 1 else None),
                    )
                    for index, (grant_id, trigger_id) in enumerate(
                        zip(grant_ids, trigger_ids, strict=True)
                    )
                ]
            )
            await seed.flush()
            seed.add(
                AgentSendAttemptModel(
                    workspace_id=workspace_id,
                    grant_id=grant_ids[1],
                    state="sending",
                    created_at=now,
                    updated_at=now,
                )
            )

        store = SessionFactoryAlphaDeliveryStore(sessions)
        provider = RecordingProvider()
        service = DeliverAlphaGrantService(store=store, telegram=provider)
        first_command = DeliverAlphaGrantCommand(
            workspace_key="mark-personal",
            grant_key="grant-1",
            now=now + timedelta(seconds=10),
        )
        first = await service.execute(first_command)
        replay = await service.execute(first_command)

        assert first.state is AgentSendAttemptState.SENT
        assert replay.idempotent_replay is True
        assert provider.calls == [
            ("-100730000001", "Database-bound exact candidate")
        ]

        crashed = await service.execute(
            DeliverAlphaGrantCommand(
                workspace_key="mark-personal",
                grant_key="grant-2",
                now=now + timedelta(seconds=11),
            )
        )
        assert crashed.state is AgentSendAttemptState.UNKNOWN
        assert crashed.error_code == "alpha_prior_attempt_outcome_unknown"
        assert len(provider.calls) == 1

        async with sessions.begin() as disable:
            await disable.execute(
                update(TransportConnector)
                .where(TransportConnector.id == connector_id)
                .values(is_enabled=False)
            )
        blocked = await service.execute(
            DeliverAlphaGrantCommand(
                workspace_key="mark-personal",
                grant_key="grant-3",
                now=now + timedelta(seconds=12),
            )
        )
        assert blocked.state is AgentSendAttemptState.BLOCKED
        assert blocked.error_code == "alpha_delivery_connector_disabled"
        assert len(provider.calls) == 1

        with pytest.raises(AlphaDeliveryError, match="grant_not_found"):
            await service.execute(
                DeliverAlphaGrantCommand(
                    workspace_key="other-workspace",
                    grant_key="grant-1",
                    now=now + timedelta(seconds=13),
                )
            )

        async with sessions() as verification:
            attempts = (
                await verification.execute(
                    select(AgentSendAttemptModel).order_by(
                        AgentSendAttemptModel.created_at,
                        AgentSendAttemptModel.id,
                    )
                )
            ).scalars().all()
            grants = (
                await verification.execute(
                    select(DeliveryGrantModel).order_by(DeliveryGrantModel.grant_key)
                )
            ).scalars().all()
            assert [item.state for item in attempts] == [
                "sending",
                "sent",
                "blocked",
            ] or [item.state for item in attempts] == [
                "sent",
                "sending",
                "blocked",
            ]
            assert {item.state for item in attempts} == {
                "sent",
                "unknown",
                "blocked",
            }
            assert grants[0].used_at is not None
            assert grants[2].revoked_at is not None
            assert (
                await verification.scalar(
                    select(func.count()).select_from(AgentSendAttemptModel)
                )
                == 3
            )
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
