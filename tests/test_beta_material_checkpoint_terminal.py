"""PostgreSQL proof that checkpoint ACK cannot strand material enrichment."""

# ruff: noqa: I001

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.infrastructure.beta_material import SqlBetaMaterialStore
from app.infrastructure.beta_space_checkpoint_batches import (
    _portable_wedding_event,
    acknowledge_beta_space_checkpoint_batch,
    next_beta_space_checkpoint_batch,
)
from app.models.beta_material import BetaMaterialJobModel
from app.models.beta_space import (
    BetaCheckpointDispatchIntentModel,
    BetaSpaceBindingModel,
    BetaSpaceEventModel,
    BetaSpaceSearchDocumentModel,
)
from app.models.canonical import TransportConnector, Workspace
from app.models.domain import Base, TelegramReceipt

NOW = datetime(2026, 8, 9, 1, tzinfo=UTC)
SPACE_ID = "spc_fcdd8e57a7e832840afcc187fb1d4b2c"
GROUP_ID = "beta-group-wedding-minsk-001"
INSTALLATION = "installation-test-terminal-material"
TENANT = "beta-user-mark-wedding-001"


class _ArtifactVerifier:
    async def verify(
        self,
        *,
        space_id: str,
        sequence_start: int,
        sequence_end: int,
        ciphertext_sha256: str,
        github_commit_sha: str,
    ) -> None:
        """Represent the durable Git artifact already proven by this integration fixture."""


@pytest.mark.anyio
async def test_ack_then_material_completion_exports_final_sequence_once() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for material checkpoint PostgreSQL test")
    schema = f"beta_material_checkpoint_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
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
        binding_id = uuid4()
        text_receipt_id = uuid4()
        material_receipt_id = uuid4()
        text_event_id = uuid4()
        material_event_id = uuid4()
        material_job_id = uuid4()
        recipient_pem = X25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        async with sessions.begin() as session:
            session.add(
                Workspace(
                    id=workspace_id,
                    key="gate",
                    display_name="Gate",
                    is_active=True,
                )
            )
            await session.flush()
            session.add(
                TransportConnector(
                    id=connector_id,
                    workspace_id=workspace_id,
                    connector_key="telegram_beta_group",
                    connector_kind="telegram_bot",
                    is_enabled=True,
                )
            )
            await session.flush()
            session.add(
                BetaSpaceBindingModel(
                    id=binding_id,
                    workspace_id=workspace_id,
                    connector_id=connector_id,
                    tenant_ref=TENANT,
                    space_ref=SPACE_ID,
                    installation_ref=INSTALLATION,
                    storage_ref=GROUP_ID,
                    telegram_chat_id="-1001",
                    subject_telegram_user_id="7",
                    consent_sha256="a" * 64,
                    checkpoint_recipient_pem=recipient_pem,
                    checkpoint_last_exported_sequence=0,
                    state="consented",
                    ready_at=NOW,
                    consented_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await session.flush()
            session.add_all(
                [
                    TelegramReceipt(
                        id=text_receipt_id,
                        bot_key="telegram_beta_group",
                        update_id=101,
                        payload={"message": {"text": "Первый готовый факт."}},
                        received_at=NOW,
                    ),
                    TelegramReceipt(
                        id=material_receipt_id,
                        bot_key="telegram_beta_group",
                        update_id=102,
                        payload={"message": {"document": {}}},
                        received_at=NOW,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    BetaSpaceEventModel(
                        id=text_event_id,
                        workspace_id=workspace_id,
                        binding_id=binding_id,
                        receipt_id=text_receipt_id,
                        update_id=101,
                        telegram_chat_id="-1001",
                        telegram_user_id="7",
                        telegram_message_id="201",
                        event_kind="fact",
                        media_kind="text",
                        payload_sha256="b" * 64,
                        checkpoint_sequence=1,
                        occurred_at=NOW,
                        received_at=NOW,
                        created_at=NOW,
                    ),
                    BetaSpaceEventModel(
                        id=material_event_id,
                        workspace_id=workspace_id,
                        binding_id=binding_id,
                        receipt_id=material_receipt_id,
                        update_id=102,
                        telegram_chat_id="-1001",
                        telegram_user_id="7",
                        telegram_message_id="202",
                        event_kind="fact",
                        media_kind="document",
                        payload_sha256="c" * 64,
                        checkpoint_sequence=2,
                        occurred_at=NOW,
                        received_at=NOW,
                        created_at=NOW,
                    ),
                ]
            )
            await session.flush()
            session.add(
                BetaMaterialJobModel(
                    id=material_job_id,
                    event_id=material_event_id,
                    workspace_id=workspace_id,
                    binding_id=binding_id,
                    media_kind="document",
                    telegram_file_id="provider-file-id",
                    sender_label="Участник",
                    occurred_at=NOW,
                    state="processing",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add(
                BetaCheckpointDispatchIntentModel(
                    workspace_id=workspace_id,
                    binding_id=binding_id,
                    space_id=SPACE_ID,
                    accepted_sequence=2,
                    dispatched_sequence=2,
                    wake_generation=2,
                    dispatched_wake_generation=2,
                    state="sent",
                    next_attempt_at=NOW,
                    settled_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

        async with sessions.begin() as session:
            first = await next_beta_space_checkpoint_batch(
                session,
                installation_ref=INSTALLATION,
                expected_user_workspace_id=TENANT,
                expected_group_id=GROUP_ID,
            )
        assert first is not None
        assert (first["sequence_start"], first["sequence_end"]) == (1, 1)
        assert first["accepted_sequence"] == 2
        async with sessions.begin() as session:
            await acknowledge_beta_space_checkpoint_batch(
                session,
                installation_ref=INSTALLATION,
                expected_user_workspace_id=TENANT,
                expected_group_id=GROUP_ID,
                sequence_start=1,
                sequence_end=1,
                ciphertext_sha256=first["manifest"]["ciphertext_sha256"],
                github_commit_sha="d" * 40,
                artifact_verifier=_ArtifactVerifier(),
            )

        async with sessions() as session:
            persisted = await SqlBetaMaterialStore(session).persist_result(
                job_id=str(material_job_id),
                source_file_sha256="e" * 64,
                extracted_text="Финальное извлечённое содержание.",
                structured_text="Финальная структура материала.",
                now=NOW,
            )
        assert persisted is True
        async with sessions() as session:
            intent = await session.get(
                BetaCheckpointDispatchIntentModel, (workspace_id, binding_id)
            )
            assert intent is not None
            assert intent.state == "pending"
            assert intent.wake_generation == 3
            assert intent.dispatched_wake_generation == 2
            search_document = await session.get(
                BetaSpaceSearchDocumentModel,
                material_event_id,
            )
            assert search_document is not None
            assert search_document.workspace_id == workspace_id
            assert search_document.binding_id == binding_id
            assert "Финальная структура материала." in search_document.content_text
            assert "Финальное извлечённое содержание." in search_document.content_text
            row = (
                await session.execute(
                    select(
                        BetaSpaceBindingModel,
                        BetaSpaceEventModel,
                        TelegramReceipt,
                        BetaMaterialJobModel,
                    )
                    .join(
                        BetaSpaceEventModel,
                        BetaSpaceEventModel.binding_id == BetaSpaceBindingModel.id,
                    )
                    .join(
                        TelegramReceipt,
                        TelegramReceipt.id == BetaSpaceEventModel.receipt_id,
                    )
                    .join(
                        BetaMaterialJobModel,
                        BetaMaterialJobModel.event_id == BetaSpaceEventModel.id,
                    )
                    .where(BetaSpaceEventModel.id == material_event_id)
                )
            ).one()
            projection = _portable_wedding_event(
                binding=row[0],
                event=row[1],
                receipt=row[2],
                voice=None,
                material=row[3],
            )
            assert "Финальное извлечённое содержание." in projection["content"]["text"]
            assert "Финальная структура материала." in projection["content"]["text"]

        async with sessions.begin() as session:
            second = await next_beta_space_checkpoint_batch(
                session,
                installation_ref=INSTALLATION,
                expected_user_workspace_id=TENANT,
                expected_group_id=GROUP_ID,
            )
        assert second is not None
        assert (second["sequence_start"], second["sequence_end"]) == (2, 2)
        async with sessions.begin() as session:
            await acknowledge_beta_space_checkpoint_batch(
                session,
                installation_ref=INSTALLATION,
                expected_user_workspace_id=TENANT,
                expected_group_id=GROUP_ID,
                sequence_start=2,
                sequence_end=2,
                ciphertext_sha256=second["manifest"]["ciphertext_sha256"],
                github_commit_sha="f" * 40,
                artifact_verifier=_ArtifactVerifier(),
            )
        async with sessions.begin() as session:
            assert (
                await next_beta_space_checkpoint_batch(
                    session,
                    installation_ref=INSTALLATION,
                    expected_user_workspace_id=TENANT,
                    expected_group_id=GROUP_ID,
                )
                is None
            )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
