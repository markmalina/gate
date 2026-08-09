"""PostgreSQL proof for audit B-005 exact Beta scope invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.infrastructure.beta_space import (
    SqlBetaSpaceStore,
    enqueue_beta_checkpoint_dispatch,
)
from app.models import agent as _agent_models  # noqa: F401
from app.models import alpha as _alpha_models  # noqa: F401
from app.models.beta_space import (
    BetaCheckpointDispatchIntentModel,
    BetaSpaceBindingModel,
    BetaSpaceEventModel,
    BetaSpaceReplyModel,
)
from app.models.canonical import TransportConnector, Workspace
from app.models.domain import Base, TelegramReceipt
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 9, 7, tzinfo=UTC)
SHARED_CHAT_ID = "-1001"
SHARED_SPACE_REF = "spc_shared_synthetic_scope"


def test_exact_scope_migration_is_additive_and_linear() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0097_beta_exact_scope.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | Sequence[str] | None = "0096_beta_binding_provenance"' in migration
    assert "uq_beta_space_event_id_binding_workspace" in migration
    assert "fk_beta_space_reply_event_scope" in migration
    assert "fk_beta_consent_delivery_event_scope" in migration
    assert "fk_beta_material_job_event_scope" in migration
    assert "fk_beta_wedding_voice_job_event_scope" in migration
    assert "fk_beta_space_work_item_event_scope" in migration
    assert "pk_beta_checkpoint_dispatch_scope" in migration
    assert "op.drop_constraint(name, table" not in migration.split("def upgrade()", 1)[1].split(
        "def downgrade()", 1
    )[0]


@pytest.mark.anyio
async def test_exact_connector_lookup_constraints_and_dispatch_scope() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for exact-scope PostgreSQL test")
    schema = f"beta_exact_scope_{uuid4().hex}"
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
        connector_a_id = uuid4()
        connector_b_id = uuid4()
        other_workspace_id = uuid4()
        other_connector_id = uuid4()
        binding_a_id = uuid4()
        binding_b_id = uuid4()
        other_binding_id = uuid4()
        receipt_id = uuid4()
        event_id = uuid4()
        async with sessions.begin() as session:
            session.add(
                Workspace(
                    id=workspace_id,
                    key="gate",
                    display_name="Gate",
                    is_active=True,
                )
            )
            session.add(
                Workspace(
                    id=other_workspace_id,
                    key="other",
                    display_name="Other",
                    is_active=True,
                )
            )
            await session.flush()
            session.add_all(
                [
                    TransportConnector(
                        id=connector_a_id,
                        workspace_id=workspace_id,
                        connector_key="telegram_beta_a",
                        connector_kind="telegram_bot",
                        is_enabled=True,
                    ),
                    TransportConnector(
                        id=other_connector_id,
                        workspace_id=other_workspace_id,
                        connector_key="telegram_beta_a",
                        connector_kind="telegram_bot",
                        is_enabled=True,
                    ),
                    TransportConnector(
                        id=connector_b_id,
                        workspace_id=workspace_id,
                        connector_key="telegram_beta_b",
                        connector_kind="telegram_bot",
                        is_enabled=True,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    BetaSpaceBindingModel(
                        id=binding_a_id,
                        workspace_id=workspace_id,
                        connector_id=connector_a_id,
                        tenant_ref="tenant-a",
                        space_ref=SHARED_SPACE_REF,
                        installation_ref="installation-a",
                        storage_ref="storage-a",
                        telegram_chat_id=SHARED_CHAT_ID,
                        subject_telegram_user_id="7",
                        consent_sha256="a" * 64,
                        state="consented",
                        ready_at=NOW,
                        consented_at=NOW,
                    ),
                    BetaSpaceBindingModel(
                        id=other_binding_id,
                        workspace_id=other_workspace_id,
                        connector_id=other_connector_id,
                        tenant_ref="tenant-other",
                        space_ref="spc_other_synthetic_scope",
                        installation_ref="installation-other",
                        storage_ref="storage-other",
                        telegram_chat_id=SHARED_CHAT_ID,
                        subject_telegram_user_id="9",
                        consent_sha256="e" * 64,
                        state="consented",
                        ready_at=NOW,
                        consented_at=NOW,
                    ),
                    BetaSpaceBindingModel(
                        id=binding_b_id,
                        workspace_id=workspace_id,
                        connector_id=connector_b_id,
                        tenant_ref="tenant-b",
                        space_ref=SHARED_SPACE_REF,
                        installation_ref="installation-b",
                        storage_ref="storage-b",
                        telegram_chat_id=SHARED_CHAT_ID,
                        subject_telegram_user_id="8",
                        consent_sha256="b" * 64,
                        state="consented",
                        ready_at=NOW,
                        consented_at=NOW,
                    ),
                    TelegramReceipt(
                        id=receipt_id,
                        bot_key="telegram_beta_a",
                        update_id=1,
                        payload={"update_id": 1},
                        received_at=NOW,
                    ),
                ]
            )
            await session.flush()
            session.add(
                BetaSpaceEventModel(
                    id=event_id,
                    workspace_id=workspace_id,
                    binding_id=binding_a_id,
                    receipt_id=receipt_id,
                    update_id=1,
                    telegram_chat_id=SHARED_CHAT_ID,
                    telegram_user_id="7",
                    telegram_message_id="11",
                    event_kind="fact",
                    media_kind="text",
                    payload_sha256="c" * 64,
                    checkpoint_sequence=1,
                    occurred_at=NOW,
                    received_at=NOW,
                )
            )

        async with sessions() as session:
            store = SqlBetaSpaceStore(session)
            binding_a = await store.resolve_binding(
                workspace_key="gate",
                connector_key="telegram_beta_a",
                telegram_chat_id=SHARED_CHAT_ID,
            )
            binding_b = await store.resolve_binding(
                workspace_key="gate",
                connector_key="telegram_beta_b",
                telegram_chat_id=SHARED_CHAT_ID,
            )
            other_binding = await store.resolve_binding(
                workspace_key="other",
                connector_key="telegram_beta_a",
                telegram_chat_id=SHARED_CHAT_ID,
            )
            assert binding_a is not None and binding_a.binding_id == str(binding_a_id)
            assert binding_b is not None and binding_b.binding_id == str(binding_b_id)
            assert binding_a.connector_id == str(connector_a_id)
            assert binding_b.connector_id == str(connector_b_id)
            assert other_binding is not None
            assert other_binding.binding_id == str(other_binding_id)
            assert other_binding.connector_id == str(other_connector_id)

        async with sessions() as session:
            session.add(
                BetaSpaceReplyModel(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    binding_id=binding_b_id,
                    event_id=event_id,
                    request_sha256="d" * 64,
                    state="generating",
                    reserved_at=NOW,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

        async with sessions.begin() as session:
            await enqueue_beta_checkpoint_dispatch(
                session,
                workspace_id=workspace_id,
                binding_id=binding_a_id,
                space_id=SHARED_SPACE_REF,
                accepted_sequence=1,
                now=NOW,
            )
            await enqueue_beta_checkpoint_dispatch(
                session,
                workspace_id=workspace_id,
                binding_id=binding_b_id,
                space_id=SHARED_SPACE_REF,
                accepted_sequence=1,
                now=NOW,
            )
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count(BetaCheckpointDispatchIntentModel.space_id))
                )
                == 2
            )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
