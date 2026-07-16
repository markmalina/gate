"""PostgreSQL proof for scoped, idempotent, queue-only Alpha voice intake."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.alpha_access import (
    AlphaAccessFacts,
    AlphaAccessInstallation,
    AlphaContentKind,
    AlphaEventKind,
    AlphaInvocationKind,
    AlphaMessageOrigin,
    AlphaSenderKind,
)
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_voice import EvaluateAlphaVoiceCommand
from app.application.alpha_voice_intake import (
    AlphaVoiceIntakeService,
    AlphaVoiceQueueCode,
    AlphaVoiceQueueDisposition,
)
from app.application.canonical_messaging import DialogKind
from app.core.config import get_settings
from app.infrastructure.alpha_voice_intake import (
    SqlAlphaVoiceQueueRepository,
    hash_telegram_file_unique_id,
)
from app.models.agent import (
    AgentDefinitionModel,
    AgentDeploymentModel,
    InstructionRevisionModel,
)
from app.models.alpha import AgentInstallationModel
from app.models.alpha_voice import AlphaVoiceJobModel, CanonicalVoiceMediaModel
from app.models.canonical import CanonicalMessage, Dialog, TransportConnector, Workspace
from app.models.domain import Base
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _access() -> AlphaAccessFacts:
    installation = AlphaAccessInstallation(
        installation_ref="voice-installation-ref",
        dialog_label="Alpha Group",
        dialog_kind=DialogKind.SUPERGROUP,
        state=AlphaInstallationState.ACTIVE,
        owner_approved=True,
    )
    return AlphaAccessFacts(
        installation=installation,
        dialog_kind=DialogKind.SUPERGROUP,
        event_kind=AlphaEventKind.MESSAGE,
        origin=AlphaMessageOrigin.INBOUND,
        sender_kind=AlphaSenderKind.HUMAN,
        content_kind=AlphaContentKind.VOICE,
        connector_enabled=True,
        invocation_kind=AlphaInvocationKind.REPLY_TO_ALPHA,
    )


def _command(message_id: object, *, file_id: str = "provider-file-id") -> EvaluateAlphaVoiceCommand:
    return EvaluateAlphaVoiceCommand(
        workspace_key="voice-workspace",
        canonical_message_id=str(message_id),
        access=_access(),
        media_kind="voice",
        voice={
            "file_id": file_id,
            "file_unique_id": "provider-file-unique-id-sensitive",
            "duration": 30,
            "file_size": 8192,
            "mime_type": "audio/ogg; codecs=opus",
        },
    )


@pytest.mark.anyio
async def test_postgres_voice_intake_is_scoped_idempotent_and_queue_only() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Alpha voice PostgreSQL test")

    schema = f"alpha_voice_{uuid4().hex}"
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
        installed_dialog_id = uuid4()
        other_dialog_id = uuid4()
        definition_id = uuid4()
        revision_id = uuid4()
        deployment_id = uuid4()
        installation_id = uuid4()
        voice_message_id = uuid4()
        other_message_id = uuid4()
        paused_message_id = uuid4()
        now = datetime(2026, 7, 16, 12, tzinfo=UTC)

        async with sessions.begin() as seed:
            seed.add(
                Workspace(
                    id=workspace_id,
                    key="voice-workspace",
                    display_name="Voice workspace",
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
                        id=installed_dialog_id,
                        workspace_id=workspace_id,
                        connector_id=connector_id,
                        provider_dialog_id="-100700000001",
                        dialog_kind="supergroup",
                    ),
                    Dialog(
                        id=other_dialog_id,
                        workspace_id=workspace_id,
                        connector_id=connector_id,
                        provider_dialog_id="-100700000002",
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
                    revision_key="alpha-voice-fixture-v1",
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
                    deployment_key="alpha-voice-fixture",
                    runtime_profile="fixture",
                    model_profile="none",
                    policy_version="alpha-voice-v1",
                    state="active",
                )
            )
            await seed.flush()
            seed.add(
                AgentInstallationModel(
                    id=installation_id,
                    workspace_id=workspace_id,
                    connector_id=connector_id,
                    dialog_id=installed_dialog_id,
                    deployment_id=deployment_id,
                    instruction_revision_id=revision_id,
                    installation_key="voice-installation-ref",
                    dialog_label="Alpha Group",
                    state="active",
                    max_turns_per_window=5,
                    rate_window_seconds=60,
                    max_tokens_per_turn=2048,
                )
            )
            seed.add_all(
                [
                    CanonicalMessage(
                        id=voice_message_id,
                        workspace_id=workspace_id,
                        dialog_id=installed_dialog_id,
                        receipt_id=None,
                        provider_message_id="voice-1",
                        provider_sender_id="person-1",
                        direction="inbound",
                        text_content=None,
                        occurred_at=now,
                    ),
                    CanonicalMessage(
                        id=other_message_id,
                        workspace_id=workspace_id,
                        dialog_id=other_dialog_id,
                        receipt_id=None,
                        provider_message_id="voice-other",
                        provider_sender_id="person-2",
                        direction="inbound",
                        text_content=None,
                        occurred_at=now,
                    ),
                    CanonicalMessage(
                        id=paused_message_id,
                        workspace_id=workspace_id,
                        dialog_id=installed_dialog_id,
                        receipt_id=None,
                        provider_message_id="voice-paused",
                        provider_sender_id="person-1",
                        direction="inbound",
                        text_content=None,
                        occurred_at=now,
                    ),
                ]
            )

        async with sessions.begin() as session:
            service = AlphaVoiceIntakeService(SqlAlphaVoiceQueueRepository(session))
            first = await service.intake(_command(voice_message_id))
            replay = await service.intake(_command(voice_message_id))

            assert first.disposition is AlphaVoiceQueueDisposition.QUEUED
            assert replay.disposition is AlphaVoiceQueueDisposition.DUPLICATE
            assert first.job_id == replay.job_id
            assert await session.scalar(select(func.count()).select_from(AlphaVoiceJobModel)) == 1
            assert (
                await session.scalar(
                    select(func.count()).select_from(CanonicalVoiceMediaModel)
                )
                == 1
            )

            job = await session.scalar(select(AlphaVoiceJobModel))
            assert job is not None
            assert job.state == "queued"
            assert job.attempts == 0
            assert job.version == 1
            assert job.provider_operation_ref is None
            assert job.transcript_text is None
            assert job.error_code is None

            media = await session.scalar(select(CanonicalVoiceMediaModel))
            assert media is not None
            assert media.file_unique_id_sha256 == hash_telegram_file_unique_id(
                "provider-file-unique-id-sensitive"
            )
            assert media.mime_type == "audio/ogg"
            assert "provider-file-unique-id-sensitive" not in repr(media)

            wrong_dialog = await service.intake(_command(other_message_id))
            assert wrong_dialog.disposition is AlphaVoiceQueueDisposition.TERMINAL
            assert wrong_dialog.code == AlphaVoiceQueueCode.DIALOG_SCOPE_MISMATCH.value

            installation = await session.get(AgentInstallationModel, installation_id)
            assert installation is not None
            installation.state = "paused"
            await session.flush()
            stale_policy = await service.intake(_command(paused_message_id))
            assert stale_policy.disposition is AlphaVoiceQueueDisposition.TERMINAL
            assert stale_policy.code == AlphaVoiceQueueCode.INSTALLATION_INACTIVE.value
            assert await session.scalar(select(func.count()).select_from(AlphaVoiceJobModel)) == 1
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
