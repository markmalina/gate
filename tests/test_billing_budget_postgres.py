"""PostgreSQL concurrency proof for the provider-neutral hard budget."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.billing import (
    BillingDomainError,
    BillingProduct,
    BudgetReservationState,
    ReserveBudgetCommand,
    UsageAttribution,
    UsageOperation,
    UsageUnit,
)
from app.infrastructure.billing_budget import SqlBillingBudgetService
from app.models.billing import (
    BillingBudgetReservationModel,
    BillingEntitlementModel,
    BillingLedgerEntryModel,
    BillingUsageEventModel,
)
from app.models.canonical import Workspace
from app.models.domain import Base
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def postgres_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL billing tests")

    schema = f"billing_budget_test_{uuid4().hex}"
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
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


def attribution() -> UsageAttribution:
    return UsageAttribution(
        workspace_key="mark-personal",
        product=BillingProduct.ALPHA,
        scope_kind="installation",
        scope_ref="opaque-alpha-installation",
    )


def command(key: str, amount: int) -> ReserveBudgetCommand:
    return ReserveBudgetCommand(
        reservation_key=key,
        attribution=attribution(),
        operation=UsageOperation.MODEL_RESPONSE,
        requested_microusd=amount,
        occurred_at=NOW,
    )


async def seed_budget(
    sessions: async_sessionmaker[AsyncSession],
    *,
    hard_limit_microusd: int = 50_000_000,
) -> None:
    workspace_id = uuid4()
    async with sessions() as session, session.begin():
        session.add(
            Workspace(
                id=workspace_id,
                key="mark-personal",
                display_name="Mark",
                is_active=True,
            )
        )
        await session.flush()
        session.add(
            BillingEntitlementModel(
                workspace_id=workspace_id,
                entitlement_key="alpha-test-budget",
                product=BillingProduct.ALPHA.value,
                scope_kind="installation",
                scope_ref="opaque-alpha-installation",
                plan_key="alpha-test",
                state="active",
                valid_from=NOW,
                valid_until=None,
                hard_limit_microusd=hard_limit_microusd,
            )
        )


@pytest.mark.anyio
async def test_concurrent_reservations_cannot_jointly_exceed_hard_limit() -> None:
    async with postgres_sessions() as sessions:
        await seed_budget(sessions)
        first = SqlBillingBudgetService(sessions)
        second = SqlBillingBudgetService(sessions)

        results = await asyncio.gather(
            first.reserve(command("reservation-a", 30_000_000), period_key="2026-07"),
            second.reserve(command("reservation-b", 30_000_000), period_key="2026-07"),
        )

        assert sum(result.allowed for result in results) == 1
        assert {result.reason_code for result in results} == {
            "billing_reserved",
            "billing_hard_limit_exceeded",
        }


@pytest.mark.anyio
async def test_exact_reservation_replay_is_idempotent() -> None:
    async with postgres_sessions() as sessions:
        await seed_budget(sessions)
        service = SqlBillingBudgetService(sessions)
        request = command("reservation-replay", 10_000_000)

        first = await service.reserve(request, period_key="2026-07")
        replay = await service.reserve(request, period_key="2026-07")

        assert first.allowed is True
        assert replay.allowed is False
        assert replay.reason_code == "billing_reservation_replayed"
        assert replay.reservation is None


@pytest.mark.anyio
async def test_reservation_key_reuse_with_new_amount_fails_closed() -> None:
    async with postgres_sessions() as sessions:
        await seed_budget(sessions)
        service = SqlBillingBudgetService(sessions)
        await service.reserve(
            command("reservation-conflict", 10_000_000),
            period_key="2026-07",
        )

        with pytest.raises(
            BillingDomainError,
            match="billing_reservation_key_reused",
        ):
            await service.reserve(
                command("reservation-conflict", 11_000_000),
                period_key="2026-07",
            )


@pytest.mark.anyio
async def test_settlement_persists_matching_usage_and_cost_ledger() -> None:
    async with postgres_sessions() as sessions:
        await seed_budget(sessions)
        service = SqlBillingBudgetService(sessions)
        admission = await service.reserve(
            command("reservation-settle", 10_000),
            period_key="2026-07",
        )
        assert admission.reservation is not None

        await service.settle(
            admission.reservation,
            actual_microusd=1_100,
            quantity=820,
            unit=UsageUnit.TOKENS,
            provider_operation_ref="provider-request-1",
            occurred_at=NOW,
        )

        async with sessions() as session:
            reservation = await session.scalar(
                select(BillingBudgetReservationModel).where(
                    BillingBudgetReservationModel.reservation_key
                    == "reservation-settle"
                )
            )
            usage = await session.scalar(select(BillingUsageEventModel))
            ledger = await session.scalar(select(BillingLedgerEntryModel))

        assert reservation is not None
        assert reservation.state == BudgetReservationState.SETTLED.value
        assert reservation.settled_microusd == 1_100
        assert usage is not None
        assert usage.quantity == 820
        assert usage.unit == UsageUnit.TOKENS.value
        assert usage.cost_microusd == 1_100
        assert usage.provider_operation_ref == "provider-request-1"
        assert ledger is not None
        assert ledger.amount_microusd == 1_100
        assert ledger.source_event_key == usage.event_key


@pytest.mark.anyio
async def test_settlement_above_reservation_is_persisted_unknown() -> None:
    async with postgres_sessions() as sessions:
        await seed_budget(sessions)
        service = SqlBillingBudgetService(sessions)
        admission = await service.reserve(
            command("reservation-overflow", 1_000),
            period_key="2026-07",
        )
        assert admission.reservation is not None

        with pytest.raises(
            BillingDomainError,
            match="billing_settlement_exceeds_reservation",
        ):
            await service.settle(
                admission.reservation,
                actual_microusd=1_001,
                quantity=820,
                unit=UsageUnit.TOKENS,
                provider_operation_ref="provider-request-overflow",
                occurred_at=NOW,
            )

        async with sessions() as session:
            reservation = await session.scalar(
                select(BillingBudgetReservationModel).where(
                    BillingBudgetReservationModel.reservation_key
                    == "reservation-overflow"
                )
            )
            usage_rows = await session.scalars(select(BillingUsageEventModel))
            usage_count = len(usage_rows.all())

        assert reservation is not None
        assert reservation.state == BudgetReservationState.UNKNOWN.value
        assert reservation.error_code == "billing_settlement_exceeds_reservation"
        assert usage_count == 0
