"""Real PostgreSQL fleet renewal acceptance; virtual clock, zero provider calls."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.worker_health import WorkerHealthRegistry
from app.infrastructure.reply_sequence_epoch import (
    binding_digest,
    install_verified_epoch,
    observe_process,
)
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyContinuityRenewal,
    ReplyDispatchControl,
    ReplyIngressProcess,
    ReplySequence,
)
from app.worker.reply_sequence_continuity import reconcile_continuity_once
from app.worker.reply_sequence_dispatch import dispatch_claim
from sqlalchemy import select

from test_reply_sends_postgres import _seed_reply
from test_reply_sequence_dispatch_postgres import FakeTelegram, queue, runtime_fixture


@pytest.fixture
def anyio_backend():
    return "asyncio"


class VirtualRegistry(WorkerHealthRegistry):
    def __init__(self, now):
        super().__init__()
        self.now = now
        for name in ("telegram", "reply_sequence"):
            self.register(name, enabled=True)
            self.record_success(name, observed_at=now)

    def snapshot(self, *, observed_at=None):
        return super().snapshot(observed_at=self.now)

    def tick(self, now):
        self.now = now
        for name in ("telegram", "reply_sequence"):
            self.record_success(name, observed_at=now)


async def prepare_epoch(runtime, sessions):
    """Seed actual observations then exercise the production protected bootstrap API."""
    runtime.settings.deployment_sha = "b" * 40
    registry = VirtualRegistry(datetime.now(UTC))
    await observe_process(runtime, registry)
    async with sessions() as session:
        now = await runtime.db_now(session)
        control = await session.get(ReplyDispatchControl, runtime.workspace)
        control.mode = "disabled"
        await session.flush()
        connector = (await session.scalars(select(ReplyConnectorAdmission))).one()
        process = await session.get(ReplyIngressProcess, (runtime.workspace, runtime.owner))
        await install_verified_epoch(
            session,
            workspace=runtime.workspace,
            evidence_sha256=uuid4().hex + uuid4().hex,
            verified_at=now,
            bot_resource_id=runtime.bot_resource,
            members=[
                {
                    "boot_id": process.boot_id,
                    "source_sha": process.source_sha,
                    "config_sha256": process.config_sha256,
                }
            ],
            binding_sha256=await binding_digest(session),
            expected_connector_version=connector.version,
        )
        control.mode = "parallel"
        await session.commit()
    return now, registry


@pytest.mark.anyio
async def test_active_series_survives_66_minutes_and_concurrent_renewers(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=50)
        start, registry = await prepare_epoch(runtime, sessions)
        queued = await queue(runtime, sessions, seed)
        async with sessions() as session:
            start = await runtime.db_now(session)
        clock = start

        async def db_now(_session):
            return clock

        monkeypatch.setattr(runtime, "db_now", db_now)
        telegram = FakeTelegram()
        first_claim = await runtime.claim(queued.sequence_id)
        assert first_claim is not None
        await dispatch_claim(runtime, telegram, first_claim)
        assert len(telegram.calls) == 1
        # Durable rate-limit wait is a legitimate long-running series, without
        # preserving or renewing a stale sender claim/permit for an hour.
        await runtime.apply_cooldown(first_claim, retry_after=65 * 60)
        async with sessions() as session:
            generation = (await session.get(ReplyDispatchControl, runtime.workspace)).generation
            first = await session.get(ReplySequence, queued.sequence_id)
            assert first.next_position == 1 and first.claim_token is None
            first_due = first.next_due_at
        for minute in range(1, 67):
            clock = start + timedelta(minutes=minute)
            registry.tick(clock)
            await observe_process(runtime, registry)
            # Both see the same version; lock loser reloads the committed renewal.
            assert await asyncio.gather(
                reconcile_continuity_once(runtime), reconcile_continuity_once(runtime)
            ) == [0, 0]
        next_claim = await runtime.claim(queued.sequence_id)
        assert next_claim is not None and next_claim.token != first_claim.token
        await dispatch_claim(runtime, telegram, next_claim)
        assert len(telegram.calls) == 2
        async with sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            sequence = await session.get(ReplySequence, queued.sequence_id)
            renewals = (await session.scalars(select(ReplyContinuityRenewal))).all()
            assert len(renewals) == 16
            assert len({r.connector_version for r in renewals}) == 16
            assert control.generation == generation and control.mode == "parallel"
            assert sequence.state == "active"
            assert sequence.claim_token is None
            assert sequence.next_position == 2
            assert sequence.next_due_at == clock + timedelta(seconds=60)
            assert sequence.next_due_at > first_due + timedelta(hours=1)
            assert sequence.dispatch_generation == generation
            connector = (await session.scalars(select(ReplyConnectorAdmission))).one()
            assert connector.continuity_until > clock
            assert all(
                (r.continuity_until - r.observed_at).total_seconds() <= 3600 for r in renewals
            )
        clock += timedelta(seconds=59)
        assert await runtime.claim(queued.sequence_id) is None
        clock += timedelta(seconds=1)
        third_claim = await runtime.claim(queued.sequence_id)
        assert third_claim is not None
        await runtime.release_preflight(third_claim)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["missing", "extra", "gap", "binding", "expiry"])
async def test_fleet_loss_stops_series_and_cannot_renew_into_recovery(monkeypatch, failure):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        start, registry = await prepare_epoch(runtime, sessions)
        queued = await queue(runtime, sessions, seed)
        clock = start + timedelta(seconds=240)

        async def db_now(_session):
            return clock

        monkeypatch.setattr(runtime, "db_now", db_now)
        registry.tick(clock)
        if failure not in {"missing", "gap"}:
            # A fresh heartbeat normally exists every 30s, not only at renewal.
            async with sessions() as session:
                process = await session.get(ReplyIngressProcess, (runtime.workspace, runtime.owner))
                process.observed_at = clock - timedelta(seconds=30)
                await session.commit()
            await observe_process(runtime, registry)
        elif failure == "gap":
            await observe_process(runtime, registry)  # latches a 240s observation gap
        async with sessions() as session:
            connector = (await session.scalars(select(ReplyConnectorAdmission))).one()
            if failure == "extra":
                process = await session.get(ReplyIngressProcess, (runtime.workspace, runtime.owner))
                session.add(
                    ReplyIngressProcess(
                        workspace_ref=runtime.workspace,
                        boot_id="f" * 32,
                        source_sha=process.source_sha,
                        config_sha256=process.config_sha256,
                        bot_resource_id=process.bot_resource_id,
                        observed_at=clock,
                        workers=process.workers,
                    )
                )
            elif failure == "binding":
                await runtime.invalidate_ingress("connection_changed")
            elif failure == "expiry":
                connector.continuity_until = clock
            await session.commit()
        assert await reconcile_continuity_once(runtime) == 1
        # Even now fresh local observations cannot rearm the expired/gapped epoch.
        registry.tick(clock + timedelta(seconds=30))
        clock += timedelta(seconds=30)
        await observe_process(runtime, registry)
        assert await reconcile_continuity_once(runtime) == 0
        async with sessions() as session:
            sequence = await session.get(ReplySequence, queued.sequence_id)
            assert sequence.state == "stopped"
            assert (await session.get(ReplyDispatchControl, runtime.workspace)).mode == "disabled"
            assert (await session.scalars(select(ReplyConnectorAdmission))).one().state != "healthy"


@pytest.mark.anyio
async def test_healthy_renewal_preserves_fresh_claim_without_extending_lease(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        seed = await _seed_reply(sessions, draft_count=2)
        start, registry = await prepare_epoch(runtime, sessions)
        queued = await queue(runtime, sessions, seed)
        async with sessions() as session:
            start = await runtime.db_now(session)
        clock = start

        async def db_now(_session):
            return clock

        monkeypatch.setattr(runtime, "db_now", db_now)
        for minute in range(1, 5):
            clock = start + timedelta(minutes=minute)
            registry.tick(clock)
            await observe_process(runtime, registry)
        claim = await runtime.claim(queued.sequence_id)
        assert claim is not None
        async with sessions() as session:
            row = await session.get(ReplySequence, queued.sequence_id)
            lease = row.claim_expires_at
            generation = row.dispatch_generation
        assert await reconcile_continuity_once(runtime) == 0
        async with sessions() as session:
            row = await session.get(ReplySequence, queued.sequence_id)
            assert row.claim_token == claim.token
            assert row.claim_expires_at == lease
            assert row.dispatch_generation == generation
        telegram = FakeTelegram()
        await dispatch_claim(runtime, telegram, claim)
        assert len(telegram.calls) == 1


@pytest.mark.anyio
async def test_completed_idle_database_poll_can_bootstrap_while_admission_stays_closed():
    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        async with sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            control.mode = "disabled"
            connector = (await session.scalars(select(ReplyConnectorAdmission))).one()
            connector.state = "blocked"
            connector.epoch_id = None
            await session.commit()
        assert await runtime.health_reason() == "sequence_dispatch_disabled"
        assert await runtime.health_reason(closed_dispatch_ok=True) is None
        # The successful closed worker visit does not itself install/arm anything.
        async with sessions() as session:
            connector = (await session.scalars(select(ReplyConnectorAdmission))).one()
            assert connector.state == "blocked" and connector.epoch_id is None
        await prepare_epoch(runtime, sessions)
        assert await runtime.health_reason() is None
