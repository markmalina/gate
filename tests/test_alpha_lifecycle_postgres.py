"""PostgreSQL lifecycle and usage guards for Alpha installations."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import (
    AlphaAccessReason,
    AlphaInstallationActionCommand,
    AlphaLifecycleError,
    AlphaLifecycleService,
    ApproveAlphaInstallationCommand,
    DiscoverAlphaInstallationCommand,
    RecordAlphaUsageCommand,
    ReserveAlphaUsageCommand,
)
from app.application.canonical_messaging import DialogKind
from app.core.config import get_settings
from app.infrastructure.alpha_lifecycle import SqlAlphaLifecycleRepository
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    InstructionRevisionModel,
)
from app.models.alpha import AlphaUsageRecordModel
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace
from app.models.domain import Base
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.anyio
async def test_discovery_approval_gating_and_usage_are_transactional() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Alpha lifecycle test")

    schema = f"alpha_lifecycle_{uuid4().hex}"
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
        workspace_id = uuid4()
        connector_id = uuid4()
        definition_id = uuid4()
        revision_id = uuid4()
        deployment_id = uuid4()
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
            seed.add_all(
                [
                    TransportConnector(
                        id=connector_id,
                        workspace_id=workspace_id,
                        connector_key="alpha_bot",
                        connector_kind="telegram_bot",
                        is_enabled=False,
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
                    revision_key="alpha-revision-v1",
                    source_repository="markmalina/alpha",
                    source_path="prompts/gate/alpha-v1.md",
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
                    policy_version="alpha-v1",
                    state="active",
                )
            )

        async with sessions() as session:
            service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
            discovery = DiscoverAlphaInstallationCommand(
                workspace_key="mark-personal",
                connector_key="alpha_bot",
                provider_dialog_id="-100730000001",
                dialog_kind=DialogKind.SUPERGROUP,
                dialog_label="Alpha canary",
                bot_is_member=True,
            )
            pending = await service.discover(discovery)
            replay = await service.discover(discovery)
            assert pending.state is AlphaInstallationState.PENDING_APPROVAL
            assert replay.installation_key == pending.installation_key
            assert pending.deployment_key is None
            await session.commit()

            blocked = await service.reserve_usage(
                ReserveAlphaUsageCommand(
                    workspace_key="mark-personal",
                    installation_key=pending.installation_key,
                    trigger_message_id=str(uuid4()),
                    reserved_tokens=100,
                    now=datetime(2026, 7, 15, tzinfo=UTC),
                )
            )
            assert blocked.reason is AlphaAccessReason.INSTALLATION_INACTIVE

            approved = await service.approve(
                ApproveAlphaInstallationCommand(
                    workspace_key="mark-personal",
                    installation_key=pending.installation_key,
                    deployment_key="alpha-deployment-v1",
                    revision_key="alpha-revision-v1",
                    max_turns_per_window=1,
                    rate_window_seconds=60,
                    max_tokens_per_turn=200,
                )
            )
            assert approved.state is AlphaInstallationState.ACTIVE
            disabled = await service.reserve_usage(
                ReserveAlphaUsageCommand(
                    workspace_key="mark-personal",
                    installation_key=pending.installation_key,
                    trigger_message_id=str(uuid4()),
                    reserved_tokens=100,
                    now=datetime(2026, 7, 15, tzinfo=UTC),
                )
            )
            assert disabled.reason is AlphaAccessReason.CONNECTOR_DISABLED
            connector = await session.get(TransportConnector, connector_id)
            assert connector is not None
            connector.is_enabled = True
            await session.commit()

            dialog_id = await session.scalar(
                select(Dialog.id).where(
                    Dialog.workspace_id == workspace_id,
                    Dialog.connector_id == connector_id,
                )
            )
            assert dialog_id is not None
            trigger_one = uuid4()
            trigger_two = uuid4()
            session.add_all(
                [
                    CanonicalMessage(
                        id=trigger_one,
                        workspace_id=workspace_id,
                        dialog_id=dialog_id,
                        provider_message_id="message-1",
                        provider_sender_id="human-1",
                        direction="inbound",
                        occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                    ),
                    CanonicalMessage(
                        id=trigger_two,
                        workspace_id=workspace_id,
                        dialog_id=dialog_id,
                        provider_message_id="message-2",
                        provider_sender_id="human-1",
                        direction="inbound",
                        occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                    ),
                ]
            )
            await session.commit()

            now = datetime(2026, 7, 15, 12, tzinfo=UTC)
            first_command = ReserveAlphaUsageCommand(
                workspace_key="mark-personal",
                installation_key=pending.installation_key,
                trigger_message_id=str(trigger_one),
                reserved_tokens=150,
                now=now,
            )
            first = await service.reserve_usage(first_command)
            duplicate = await service.reserve_usage(first_command)
            limited = await service.reserve_usage(
                ReserveAlphaUsageCommand(
                    workspace_key="mark-personal",
                    installation_key=pending.installation_key,
                    trigger_message_id=str(trigger_two),
                    reserved_tokens=150,
                    now=now + timedelta(seconds=1),
                )
            )
            assert first.allowed is True
            assert duplicate.idempotent_replay is True
            assert limited.reason is AlphaAccessReason.RATE_LIMITED
            assert first.usage_key is not None
            await service.record_usage(
                RecordAlphaUsageCommand(
                    workspace_key="mark-personal",
                    usage_key=first.usage_key,
                    input_tokens=40,
                    output_tokens=20,
                )
            )
            await session.commit()

            status_items = await service.list_status("mark-personal")
            assert status_items[0].reserved_turns == 1
            assert status_items[0].recorded_tokens == 60

            paused = await service.pause(
                AlphaInstallationActionCommand(
                    workspace_key="mark-personal",
                    installation_key=pending.installation_key,
                )
            )
            assert paused.state is AlphaInstallationState.PAUSED
            await session.commit()
            inactive = await service.reserve_usage(
                ReserveAlphaUsageCommand(
                    workspace_key="mark-personal",
                    installation_key=pending.installation_key,
                    trigger_message_id=str(trigger_two),
                    reserved_tokens=100,
                    now=now + timedelta(minutes=2),
                )
            )
            assert inactive.reason is AlphaAccessReason.INSTALLATION_INACTIVE

            with pytest.raises(
                AlphaLifecycleError,
                match="state changed",
            ):
                await service.resume(
                    AlphaInstallationActionCommand(
                        workspace_key="mark-personal",
                        installation_key=pending.installation_key,
                        expected_state=AlphaInstallationState.ACTIVE,
                    )
                )
            private_dialog = await session.get(Dialog, dialog_id)
            assert private_dialog is not None
            private_dialog.dialog_kind = DialogKind.PRIVATE.value
            await session.commit()

            resumed = await service.resume(
                AlphaInstallationActionCommand(
                    workspace_key="mark-personal",
                    installation_key=pending.installation_key,
                    expected_state=AlphaInstallationState.PAUSED,
                )
            )
            assert resumed.state is AlphaInstallationState.ACTIVE
            await session.commit()

            unknown = await service.reserve_usage(
                ReserveAlphaUsageCommand(
                    workspace_key="mark-personal",
                    installation_key="missing-installation",
                    trigger_message_id=str(trigger_two),
                    reserved_tokens=100,
                    now=now + timedelta(minutes=2),
                )
            )
            assert unknown.reason is AlphaAccessReason.UNKNOWN_INSTALLATION

            revoked = await service.revoke(
                AlphaInstallationActionCommand(
                    workspace_key="mark-personal",
                    installation_key=pending.installation_key,
                )
            )
            assert revoked.state is AlphaInstallationState.REVOKED
            await session.commit()
            usage_rows = await session.execute(select(AlphaUsageRecordModel))
            usage_count = len(usage_rows.scalars().all())
            assert usage_count == 1
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
