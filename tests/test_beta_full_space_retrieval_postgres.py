"""PostgreSQL proof for exact-scope full-history material-aware retrieval."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import get_settings
from app.infrastructure.beta_wedding_reply import SqlBetaWeddingReplyStore
from app.models.beta_space import (
    BetaSpaceBindingModel,
    BetaSpaceEventModel,
    BetaSpaceSearchDocumentModel,
)
from app.models.canonical import TransportConnector, Workspace
from app.models.domain import Base, TelegramReceipt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_full_history_finds_old_text_and_terminal_material_in_exact_space() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for full-history PostgreSQL test")
    schema = f"beta_full_retrieval_{uuid.uuid4().hex}"
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
        workspace_id = uuid.uuid4()
        other_workspace_id = uuid.uuid4()
        connector_id = uuid.uuid4()
        other_connector_id = uuid.uuid4()
        binding_id = uuid.uuid4()
        other_binding_id = uuid.uuid4()
        binding = BetaSpaceBindingModel(
            id=binding_id,
            workspace_id=workspace_id,
            connector_id=connector_id,
            tenant_ref="beta-user-mark-wedding-001",
            space_ref="spc_fcdd8e57a7e832840afcc187fb1d4b2c",
            installation_ref="installation-full-retrieval-primary",
            storage_ref="beta-group-full-retrieval-primary",
            telegram_chat_id="-100100",
            subject_telegram_user_id="7",
            consent_sha256="a" * 64,
            state="consented",
            ready_at=NOW,
            consented_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        other_binding = BetaSpaceBindingModel(
            id=other_binding_id,
            workspace_id=other_workspace_id,
            connector_id=other_connector_id,
            tenant_ref="beta-user-other-001",
            space_ref="spc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            installation_ref="installation-full-retrieval-other",
            storage_ref="beta-group-full-retrieval-other",
            telegram_chat_id="-100200",
            subject_telegram_user_id="8",
            consent_sha256="b" * 64,
            state="consented",
            ready_at=NOW,
            consented_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        async with sessions.begin() as session:
            session.add_all(
                [
                    Workspace(
                        id=workspace_id,
                        key="retrieval-primary",
                        display_name="Retrieval Primary",
                        is_active=True,
                    ),
                    Workspace(
                        id=other_workspace_id,
                        key="retrieval-other",
                        display_name="Retrieval Other",
                        is_active=True,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    TransportConnector(
                        id=connector_id,
                        workspace_id=workspace_id,
                        connector_key="telegram_beta_group",
                        connector_kind="telegram_bot",
                        is_enabled=True,
                    ),
                    TransportConnector(
                        id=other_connector_id,
                        workspace_id=other_workspace_id,
                        connector_key="telegram_beta_group",
                        connector_kind="telegram_bot",
                        is_enabled=True,
                    ),
                ]
            )
            await session.flush()
            session.add_all([binding, other_binding])
            await session.flush()

            documents = [
                (
                    "Бюджет площадки утверждён: 120 000 рублей.",
                    "text",
                    NOW - timedelta(days=2),
                ),
                (
                    "Меню дегустации сохранено из обработанного документа.",
                    "document",
                    NOW - timedelta(days=1),
                ),
            ]
            documents.extend(
                (f"Недавняя нерелевантная заметка {index}", "text", NOW + timedelta(seconds=index))
                for index in range(205)
            )
            receipts: list[TelegramReceipt] = []
            events: list[BetaSpaceEventModel] = []
            search_documents: list[BetaSpaceSearchDocumentModel] = []
            for sequence, (content, source_kind, occurred_at) in enumerate(documents, 1):
                receipt_id = uuid.uuid4()
                event_id = uuid.uuid4()
                receipts.append(
                    TelegramReceipt(
                        id=receipt_id,
                        bot_key="telegram_beta_group",
                        update_id=sequence,
                        payload={},
                        received_at=occurred_at,
                    )
                )
                events.append(
                    BetaSpaceEventModel(
                        id=event_id,
                        workspace_id=workspace_id,
                        binding_id=binding_id,
                        receipt_id=receipt_id,
                        update_id=sequence,
                        telegram_chat_id="-100100",
                        telegram_user_id="7",
                        telegram_message_id=str(sequence),
                        event_kind="fact",
                        media_kind=source_kind,
                        payload_sha256=f"{sequence:064x}"[-64:],
                        checkpoint_sequence=sequence,
                        occurred_at=occurred_at,
                        received_at=occurred_at,
                        created_at=occurred_at,
                    )
                )
                search_documents.append(
                    BetaSpaceSearchDocumentModel(
                        event_id=event_id,
                        workspace_id=workspace_id,
                        binding_id=binding_id,
                        source_kind=source_kind,
                        sender_label="Участник",
                        content_text=content,
                        occurred_at=occurred_at,
                        created_at=occurred_at,
                        updated_at=occurred_at,
                    )
                )
            wrong_receipt_id = uuid.uuid4()
            wrong_event_id = uuid.uuid4()
            receipts.append(
                TelegramReceipt(
                    id=wrong_receipt_id,
                    bot_key="telegram_beta_group",
                    update_id=10_000,
                    payload={},
                    received_at=NOW,
                )
            )
            events.append(
                BetaSpaceEventModel(
                    id=wrong_event_id,
                    workspace_id=other_workspace_id,
                    binding_id=other_binding_id,
                    receipt_id=wrong_receipt_id,
                    update_id=10_000,
                    telegram_chat_id="-100200",
                    telegram_user_id="8",
                    telegram_message_id="10000",
                    event_kind="fact",
                    media_kind="text",
                    payload_sha256="f" * 64,
                    checkpoint_sequence=1,
                    occurred_at=NOW,
                    received_at=NOW,
                    created_at=NOW,
                )
            )
            search_documents.append(
                BetaSpaceSearchDocumentModel(
                    event_id=wrong_event_id,
                    workspace_id=other_workspace_id,
                    binding_id=other_binding_id,
                    source_kind="text",
                    sender_label="Другой Space",
                    content_text="Бюджет площадки 999 999 и чужое меню дегустации.",
                    occurred_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add_all(receipts)
            await session.flush()
            session.add_all(events)
            await session.flush()
            session.add_all(search_documents)

        async with sessions() as session:
            context = await SqlBetaWeddingReplyStore(session)._context(
                binding=binding,
                current_event_id=uuid.uuid4(),
                current_text="Какой бюджет площадки и что в меню дегустации?",
            )

        texts = [item.text for item in context]
        assert "Бюджет площадки утверждён: 120 000 рублей." in texts
        assert "Меню дегустации сохранено из обработанного документа." in texts
        assert all("999 999" not in item for item in texts)
        assert sum("Недавняя нерелевантная заметка" in item for item in texts) == 8
        assert sum(len(item) for item in texts) <= 12_000
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
