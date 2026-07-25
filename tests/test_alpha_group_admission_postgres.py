"""PostgreSQL proof for bounded Telegram Alpha group admission."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.alpha_canary import (
    AlphaCanaryError,
    AlphaMembershipAction,
    ReconcileAlphaMembershipCommand,
)
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import AlphaLifecycleService
from app.application.canonical_messaging import DialogKind
from app.core.config import get_settings
from app.infrastructure.alpha_canary import SessionFactoryAlphaCanaryStore
from app.infrastructure.alpha_lifecycle import SqlAlphaLifecycleRepository
from app.models.canonical import TransportConnector, Workspace
from app.models.domain import Base
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 7, 25, 20, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_membership_admission_is_bounded_transactional_and_idempotent() -> None:
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is required for Alpha admission test")

    schema = f"alpha_group_admission_{uuid4().hex}"
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
                    connector_key="telegram_alpha_group",
                    connector_kind="telegram_bot",
                    is_enabled=True,
                )
            )

        store = SessionFactoryAlphaCanaryStore(sessions, group_capacity=3)

        async def admit(provider_dialog_id: str, label: str):
            return await store.reconcile_membership(
                ReconcileAlphaMembershipCommand(
                    workspace_key="mark-personal",
                    connector_key="telegram_alpha_group",
                    provider_dialog_id=provider_dialog_id,
                    dialog_kind=DialogKind.SUPERGROUP,
                    dialog_label=label,
                    action=AlphaMembershipAction.PRESENT,
                    now=NOW,
                )
            )

        first = await admit("-100000000001", "Alpha Group")
        second = await admit("-100000000002", "Строй αV")
        third = await admit("-100000000003", "Майя Альфа")
        replay = await admit("-100000000003", "Майя Альфа")

        assert first.installation_key
        assert second.installation_key
        assert third.installation_key
        assert len({first.installation_key, second.installation_key, third.installation_key}) == 3
        assert replay.installation_key == third.installation_key

        with pytest.raises(
            AlphaCanaryError,
            match="alpha_canary_group_capacity_reached",
        ):
            await admit("-100000000004", "Outside capacity")

        async with sessions() as session:
            status = await AlphaLifecycleService(
                SqlAlphaLifecycleRepository(session)
            ).list_status("mark-personal")

        assert len(status) == 3
        assert {item.dialog_label for item in status} == {
            "Alpha Group",
            "Строй αV",
            "Майя Альфа",
        }
        assert all(
            item.state is AlphaInstallationState.PENDING_APPROVAL
            for item in status
        )
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
        await admin_engine.dispose()
