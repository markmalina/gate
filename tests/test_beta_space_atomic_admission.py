"""PostgreSQL proof that Beta fact admission and resumable work are atomic."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.beta_space_intake import BetaSpaceBinding, BetaSpaceSource
from app.core.config import get_settings
from app.infrastructure.beta_space import SqlBetaSpaceStore
from app.models.beta_space import (
    BetaCheckpointDispatchIntentModel,
    BetaSpaceBindingModel,
    BetaSpaceEventModel,
    BetaSpaceWorkItemModel,
)
from app.models.canonical import TransportConnector, Workspace
from app.models.domain import Base, TelegramReceipt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 9, tzinfo=UTC)
SPACE_ID = "spc_fcdd8e57a7e832840afcc187fb1d4b2c"


@pytest.mark.anyio
async def test_fact_and_typed_work_commit_together_or_not_at_all() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for atomic admission PostgreSQL test")
    schema = f"beta_atomic_{uuid4().hex}"
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
        receipt_id = uuid4()
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
                TelegramReceipt(
                    id=receipt_id,
                    bot_key="telegram_beta_group",
                    update_id=101,
                    payload={"update_id": 101},
                    received_at=NOW,
                )
            )
            session.add(
                BetaSpaceBindingModel(
                    id=binding_id,
                    workspace_id=workspace_id,
                    connector_id=connector_id,
                    tenant_ref="beta-user-mark-wedding-001",
                    space_ref=SPACE_ID,
                    installation_ref="installation-test",
                    storage_ref="storage-test",
                    telegram_chat_id="-1001",
                    subject_telegram_user_id="7",
                    consent_sha256="a" * 64,
                    state="consented",
                    ready_at=NOW,
                    consented_at=NOW,
                )
            )
        binding = BetaSpaceBinding(
            binding_id=str(binding_id),
            workspace_id=str(workspace_id),
            connector_id=str(connector_id),
            tenant_ref="beta-user-mark-wedding-001",
            space_ref=SPACE_ID,
            installation_ref="installation-test",
            telegram_chat_id="-1001",
            subject_telegram_user_id="7",
            consent_sha256="a" * 64,
            ready_at=NOW,
            state="consented",
            actor_policy="group_members",
        )
        source = BetaSpaceSource(
            receipt_id=str(receipt_id),
            update_id=101,
            telegram_chat_id="-1001",
            telegram_user_id="7",
            telegram_message_id="201",
            occurred_at=NOW,
            received_at=NOW,
            media_kind="text",
            payload_sha256="b" * 64,
        )
        async with sessions() as session:
            result = await SqlBetaSpaceStore(session).append_fact(
                binding=binding,
                source=source,
            )
            assert result.created is True
        async with sessions() as session:
            assert await session.scalar(select(func.count(BetaSpaceEventModel.id))) == 1
            work = await session.scalar(select(BetaSpaceWorkItemModel))
            assert work is not None
            assert work.kind == "reply"
            assert work.state == "pending"
            assert work.receipt_id == receipt_id
            dispatch = await session.get(
                BetaCheckpointDispatchIntentModel,
                (workspace_id, binding_id),
            )
            assert dispatch is not None
            assert dispatch.accepted_sequence == 1

        second_receipt_id = uuid4()
        async with sessions.begin() as session:
            session.add(
                TelegramReceipt(
                    id=second_receipt_id,
                    bot_key="telegram_beta_group",
                    update_id=102,
                    payload={"update_id": 102},
                    received_at=NOW,
                )
            )
        failed_source = BetaSpaceSource(
            receipt_id=str(second_receipt_id),
            update_id=102,
            telegram_chat_id="-1001",
            telegram_user_id="7",
            telegram_message_id="202",
            occurred_at=NOW,
            received_at=NOW,
            media_kind="text",
            payload_sha256="c" * 64,
        )
        async with sessions() as session:
            store = SqlBetaSpaceStore(session)

            def fail_after_event(**_kwargs: object) -> None:
                raise RuntimeError("synthetic_after_event_insert")

            store._add_work_item = fail_after_event  # type: ignore[method-assign]
            with pytest.raises(RuntimeError, match="synthetic_after_event_insert"):
                await store.append_fact(binding=binding, source=failed_source)
            await session.rollback()
        async with sessions() as session:
            assert await session.scalar(select(func.count(BetaSpaceEventModel.id))) == 1
            assert await session.scalar(select(func.count(BetaSpaceWorkItemModel.id))) == 1
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
