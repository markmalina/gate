"""PostgreSQL proof that Alpha approvals and limits stay group-isolated."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import (
    AlphaAccessReason,
    AlphaLifecycleService,
    ApproveAlphaInstallationCommand,
    DiscoverAlphaInstallationCommand,
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
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace
from app.models.domain import Base
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.anyio
async def test_two_groups_have_independent_approval_context_and_rate_limits() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Alpha multi-group test")

    schema = f"alpha_multigroup_{uuid4().hex}"
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
                        connector_key="telegram_alpha_group",
                        connector_kind="telegram_bot",
                        is_enabled=True,
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
                    revision_key="alpha-canary-v1",
                    source_repository="markmalina/Gate",
                    source_path="agent_bundles/alpha_canary_v1",
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
                    deployment_key="alpha-canary-v1-fixture",
                    runtime_profile="fixture",
                    model_profile="none",
                    policy_version="alpha-canary-v1",
                    state="active",
                )
            )

        async with sessions() as session:
            service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
            group_a = await service.discover(
                DiscoverAlphaInstallationCommand(
                    workspace_key="mark-personal",
                    connector_key="telegram_alpha_group",
                    provider_dialog_id="-100000000001",
                    dialog_kind=DialogKind.SUPERGROUP,
                    dialog_label="Alpha Group A",
                    bot_is_member=True,
                )
            )
            group_b = await service.discover(
                DiscoverAlphaInstallationCommand(
                    workspace_key="mark-personal",
                    connector_key="telegram_alpha_group",
                    provider_dialog_id="-100000000002",
                    dialog_kind=DialogKind.SUPERGROUP,
                    dialog_label="Alpha Group B",
                    bot_is_member=True,
                )
            )
            assert group_a.state is AlphaInstallationState.PENDING_APPROVAL
            assert group_b.state is AlphaInstallationState.PENDING_APPROVAL
            assert group_a.installation_key != group_b.installation_key

            group_a = await service.approve(
                ApproveAlphaInstallationCommand(
                    workspace_key="mark-personal",
                    installation_key=group_a.installation_key,
                    deployment_key="alpha-canary-v1-fixture",
                    revision_key="alpha-canary-v1",
                    max_turns_per_window=1,
                    rate_window_seconds=60,
                    max_tokens_per_turn=200,
                )
            )
            group_b = await service.approve(
                ApproveAlphaInstallationCommand(
                    workspace_key="mark-personal",
                    installation_key=group_b.installation_key,
                    deployment_key="alpha-canary-v1-fixture",
                    revision_key="alpha-canary-v1",
                    max_turns_per_window=2,
                    rate_window_seconds=60,
                    max_tokens_per_turn=400,
                )
            )
            await session.commit()

            dialogs = (
                await session.execute(
                    select(Dialog.provider_dialog_id, Dialog.id).where(
                        Dialog.workspace_id == workspace_id
                    )
                )
            ).all()
            dialog_ids = {provider_id: dialog_id for provider_id, dialog_id in dialogs}
            group_a_message_1 = uuid4()
            group_a_message_2 = uuid4()
            group_b_message_1 = uuid4()
            session.add_all(
                [
                    CanonicalMessage(
                        id=group_a_message_1,
                        workspace_id=workspace_id,
                        dialog_id=dialog_ids["-100000000001"],
                        provider_message_id="a-1",
                        provider_sender_id="human-a",
                        direction="inbound",
                        text_content="/alpha A1",
                        occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
                    ),
                    CanonicalMessage(
                        id=group_a_message_2,
                        workspace_id=workspace_id,
                        dialog_id=dialog_ids["-100000000001"],
                        provider_message_id="a-2",
                        provider_sender_id="human-a",
                        direction="inbound",
                        text_content="/alpha A2",
                        occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
                    ),
                    CanonicalMessage(
                        id=group_b_message_1,
                        workspace_id=workspace_id,
                        dialog_id=dialog_ids["-100000000002"],
                        provider_message_id="b-1",
                        provider_sender_id="human-b",
                        direction="inbound",
                        text_content="/alpha B1",
                        occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
                    ),
                ]
            )
            await session.commit()

            now = datetime(2026, 7, 16, 12, tzinfo=UTC)

            async def reserve(
                installation_key: str,
                trigger_id: object,
                tokens: int,
                offset_seconds: int = 0,
            ):
                return await service.reserve_usage(
                    ReserveAlphaUsageCommand(
                        workspace_key="mark-personal",
                        installation_key=installation_key,
                        trigger_message_id=str(trigger_id),
                        reserved_tokens=tokens,
                        now=now + timedelta(seconds=offset_seconds),
                    )
                )

            first_a = await reserve(group_a.installation_key, group_a_message_1, 100)
            crossed = await reserve(group_b.installation_key, group_a_message_1, 100)
            limited_a = await reserve(
                group_a.installation_key,
                group_a_message_2,
                100,
                1,
            )
            allowed_b = await reserve(
                group_b.installation_key,
                group_b_message_1,
                300,
                1,
            )

            assert first_a.allowed is True
            assert crossed.reason is AlphaAccessReason.TRIGGER_OUT_OF_SCOPE
            assert limited_a.reason is AlphaAccessReason.RATE_LIMITED
            assert allowed_b.allowed is True
            await session.commit()

            status = await service.list_status("mark-personal")
            by_label = {item.dialog_label: item for item in status}
            assert by_label["Alpha Group A"].reserved_turns == 1
            assert by_label["Alpha Group B"].reserved_turns == 1
            assert by_label["Alpha Group A"].max_turns_per_window == 1
            assert by_label["Alpha Group B"].max_turns_per_window == 2
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
