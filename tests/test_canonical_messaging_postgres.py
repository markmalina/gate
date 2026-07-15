"""PostgreSQL lifecycle guards for canonical ingress persistence."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.canonical_messaging import (
    CanonicalMessageDirection,
    CanonicalMessageEnvelope,
    ConnectorKind,
    DialogIdentity,
    DialogKind,
    WorkOwnershipDecision,
    WorkRoute,
)
from app.application.work_ownership import WorkOwnershipState
from app.core.config import get_settings
from app.infrastructure.canonical_messaging import SessionFactoryCanonicalIngressStore
from app.models.canonical import (
    CanonicalMessage,
    Dialog,
    TransportConnector,
    WorkOwnership,
    Workspace,
)
from app.models.domain import Base, TelegramReceipt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _dialog() -> DialogIdentity:
    return DialogIdentity(
        workspace_id="mark-personal",
        connector_key="telegram_business",
        connector_kind=ConnectorKind.TELEGRAM_BUSINESS,
        provider_dialog_id="730000001",
        dialog_kind=DialogKind.PRIVATE,
    )


def _message(*, receipt_id: str, provider_message_id: str) -> CanonicalMessageEnvelope:
    return CanonicalMessageEnvelope(
        workspace_id="mark-personal",
        connector_key="telegram_business",
        source_receipt_id=receipt_id,
        provider_dialog_id="730000001",
        provider_message_id=provider_message_id,
        provider_sender_id="740000001",
        direction=CanonicalMessageDirection.INBOUND,
        occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
        text_present=True,
    )


def _ownership(reason: str) -> WorkOwnershipDecision:
    return WorkOwnershipDecision(
        state=WorkOwnershipState.HUMAN_MANAGED,
        route=WorkRoute.HUMAN_REPLY_TASK,
        policy_version="ownership-v1",
        reason_code=reason,
    )


@pytest.mark.anyio
async def test_store_is_idempotent_and_advances_current_ownership_once_per_message() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for canonical ingress store test")

    schema = f"canonical_store_{uuid4().hex}"
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
        receipt_one = uuid4()
        receipt_two = uuid4()
        async with sessions.begin() as seed:
            seed.add_all(
                [
                    TelegramReceipt(
                        id=receipt_one,
                        bot_key="telegram_business",
                        update_id=501,
                        payload={"update_id": 501},
                        received_at=datetime(2026, 7, 15, tzinfo=UTC),
                    ),
                    TelegramReceipt(
                        id=receipt_two,
                        bot_key="telegram_business",
                        update_id=502,
                        payload={"update_id": 502},
                        received_at=datetime(2026, 7, 15, tzinfo=UTC),
                    ),
                ]
            )

        store = SessionFactoryCanonicalIngressStore(sessions)
        first = await store.persist(
            dialog=_dialog(),
            message=_message(
                receipt_id=str(receipt_one),
                provider_message_id="provider-message-1",
            ),
            ownership=_ownership("first_human_message"),
        )
        duplicate = await store.persist(
            dialog=_dialog(),
            message=_message(
                receipt_id=str(receipt_one),
                provider_message_id="provider-message-1",
            ),
            ownership=_ownership("first_human_message"),
        )
        second = await store.persist(
            dialog=_dialog(),
            message=_message(
                receipt_id=str(receipt_two),
                provider_message_id="provider-message-2",
            ),
            ownership=_ownership("second_human_message"),
        )

        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert duplicate.message_id == first.message_id
        assert second.duplicate is False

        async with sessions() as verify:
            counts = {
                model.__tablename__: await verify.scalar(select(func.count()).select_from(model))
                for model in (
                    Workspace,
                    TransportConnector,
                    Dialog,
                    CanonicalMessage,
                    WorkOwnership,
                )
            }
            ownership = (await verify.execute(select(WorkOwnership))).scalar_one()
            connector = (await verify.execute(select(TransportConnector))).scalar_one()

        assert counts == {
            "workspaces": 1,
            "transport_connectors": 1,
            "dialogs": 1,
            "canonical_messages": 2,
            "work_ownerships": 1,
        }
        assert ownership.version == 2
        assert ownership.reason_code == "second_human_message"
        assert connector.is_enabled is False
        assert connector.credential_secret_ref is None
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
