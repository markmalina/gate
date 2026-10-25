"""Restart, durable source failures, incident dedup and preservation; synthetic only."""

import asyncio
import importlib.util
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.infrastructure.watch_voice_journal import CURRENT_CYCLE, finish_cycle, pulse, start_cycle
from app.infrastructure.watch_voice_metrics import METRICS_SCHEMA, OBSERVATION, snapshot
from app.infrastructure.watch_voice_monitor import (
    observe_watch_voice,
    reconcile_incident,
    shared_budget,
)
from app.infrastructure.watch_voice_source_inventory import (
    SOURCE_INVENTORY_SCHEMA,
    source_inventory,
)
from app.infrastructure.watch_voice_sources import archive_source, discover, drain_sources
from app.models.billing import BillingBudgetReservationModel, BillingEntitlementModel
from app.models.canonical import Workspace
from app.models.watch_voice import (
    WatchVoiceCycle,
    WatchVoiceIncident,
    WatchVoiceNote,
    WatchVoiceSource,
)
from app.worker import watch_voice as worker
from jsonschema import validate
from sqlalchemy import (
    Column,
    DateTime,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.test_watch_voice import World, admitted_future_cursor, database, notes, settings
from tests.test_watch_voice_inventory import note


@asynccontextmanager
async def db(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            for table in (
                Workspace.__table__,
                BillingBudgetReservationModel.__table__,
                BillingEntitlementModel.__table__,
            ):
                await session.run_sync(lambda s, t=table: t.create(s.connection()))
        yield factory


def test_new_migration_preserves_originals_and_keeps_historical_times_unknown():
    path = Path("alembic/versions/0116_watch_voice_observability.py")
    spec = importlib.util.spec_from_file_location("voice_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    metadata = MetaData()
    legacy = Table(
        "watch_voice_notes",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("workspace_key", String(128)),
        Column("account_id", String(255)),
        Column("received_at", DateTime(timezone=True)),
        Column("data", LargeBinary),
        Column("transcript", Text),
    )
    with engine.begin() as conn:
        metadata.create_all(conn)
        conn.execute(
            insert(legacy).values(
                id="synthetic",
                workspace_key="synthetic",
                account_id="synthetic",
                data=b"original\x00bytes",
                transcript="полный текст",
            )
        )
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            migration.upgrade()
        row = conn.exec_driver_sql(
            "SELECT data,transcript,detected_at,provider_request_count FROM watch_voice_notes"
        ).one()
        assert row == (b"original\x00bytes", "полный текст", None, None)
        with Operations.context(context):
            migration.downgrade()  # disposable fixture only, never a live rollback
        assert conn.execute(select(legacy.c.data, legacy.c.transcript)).one() == (
            b"original\x00bytes",
            "полный текст",
        )
    engine.dispose()


def test_durable_cycle_survives_independent_reader_and_detects_deadline(tmp_path):
    async def scenario():
        cfg = settings().model_copy(update={"deployment_sha": "a" * 40})
        async with db(tmp_path) as factory:
            key = await start_cycle(factory, cfg, driver="persistent")
            context = CURRENT_CYCLE.set((factory, key))
            try:
                await pulse("normalizing", seconds=1200, owns_cursor=True)
                async with factory() as session:
                    session.add(
                        WatchVoiceNote(
                            **note(
                                1,
                                account_id="synthetic-owner",
                                received_at=datetime.now(UTC) - timedelta(minutes=20),
                            )
                        )
                    )
                    await session.commit()
                # A fresh reader has no process-local heartbeat. SQL is sufficient.
                OBSERVATION.started_at = OBSERVATION.completed_at = OBSERVATION.scanned_at = None
                reader = async_sessionmaker(factory.kw["bind"], expire_on_commit=False)
                async with reader() as session:
                    live = await snapshot(session, cfg)
                    validate(live, METRICS_SCHEMA)
                    assert live["running_driver_proven"]
                    assert live["last_cycle_id"] == key
                    assert "watch_voice_queue_no_progress" not in live["signal_codes"]
                    await session.execute(
                        update(WatchVoiceCycle)
                        .where(WatchVoiceCycle.id == key)
                        .values(deadline_at=datetime.now(UTC) - timedelta(seconds=1))
                    )
                    await session.commit()
                async with reader() as session:
                    stopped = await snapshot(session, cfg)
                    assert not stopped["running_driver_proven"]
                    assert "watch_voice_phase_deadline_exceeded" in stopped["signal_codes"]
                await finish_cycle("success")
                async with reader() as session:
                    assert (await snapshot(session, cfg))["cycle_outcome"] == "success"
            finally:
                CURRENT_CYCLE.reset(context)

    asyncio.run(scenario())


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="Real PostgreSQL concurrency required")
def test_parallel_monitors_share_one_open_incident(tmp_path):
    async def scenario():
        async with database(tmp_path) as factory:
            await asyncio.gather(
                *[
                    reconcile_incident(factory, settings(), ["watch_voice_cycle_stale"])
                    for _ in range(8)
                ]
            )
            async with factory() as session:
                rows = list((await session.scalars(select(WatchVoiceIncident))).all())
                assert len(rows) == 1 and rows[0].log_attempt_count == 1

    asyncio.run(scenario())


def test_source_lease_restart_recovers_saved_archive_without_redownload(tmp_path):
    world = World()

    async def scenario():
        cfg = settings()
        async with database(tmp_path) as factory:
            key = await discover(cfg, factory, world.entry, None)
            async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as http:
                await worker.store_entry(cfg, factory, http, "synthetic", world.entry)
                before = len(world.calls)
                async with factory() as session:
                    await session.execute(
                        update(WatchVoiceSource)
                        .where(WatchVoiceSource.id == key)
                        .values(
                            state="downloading",
                            claim_id="old",
                            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
                        )
                    )
                    await session.commit()
                await archive_source(cfg, factory, http, "synthetic", key)
                async with factory() as session:
                    row = await session.get(WatchVoiceSource, key)
                    assert row.state == "downloading" and row.attempt_count == 0
                    row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    await session.commit()
                await archive_source(cfg, factory, http, "synthetic", key)
                async with factory() as session:
                    row = await session.get(WatchVoiceSource, key)
                    assert row.state == "archived" and row.attempt_count == 1
                assert len(world.calls) == before
                assert world.provider_calls == world.posts == 0

    asyncio.run(scenario())


def test_source_retry_excludes_history_and_other_accounts(tmp_path):
    world = World()

    async def scenario():
        cfg = settings()
        now = datetime.now(UTC)
        async with database(tmp_path) as factory:
            historical = await discover(cfg, factory, world.entry, None)
            async with factory() as session:
                session.add(
                    WatchVoiceSource(
                        id=str(uuid.uuid4()),
                        workspace_key="mark-personal",
                        account_id="foreign",
                        file_id="id:foreign",
                        revision="rev",
                        filename="f.m4a",
                        source_path="/Alpha/Voice/f.m4a",
                        size_bytes=1,
                        dropbox_modified_at=now,
                        detected_at=now,
                        state="retry",
                        attempt_count=0,
                    )
                )
                await session.commit()

            def no_request(request):
                raise AssertionError("inadmissible retry reached download")

            async with httpx.AsyncClient(transport=httpx.MockTransport(no_request)) as http:
                await drain_sources(cfg, factory, http, "synthetic", now - timedelta(seconds=10))
            async with factory() as session:
                assert (await session.get(WatchVoiceSource, historical)).attempt_count == 0

    asyncio.run(scenario())


def test_incident_dedup_recovery_and_restart_use_one_episode(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="app.infrastructure.watch_voice_monitor")

    async def scenario():
        async with database(tmp_path) as factory:
            cfg = settings()
            first = await reconcile_incident(factory, cfg, ["watch_voice_cycle_stale"])
            for _ in range(4):
                await reconcile_incident(factory, cfg, ["watch_voice_cycle_stale"])
            restarted = async_sessionmaker(factory.kw["bind"], expire_on_commit=False)
            assert await reconcile_incident(restarted, cfg, ["watch_voice_cycle_stale"]) == first
            await reconcile_incident(
                restarted, cfg, ["watch_voice_cycle_stale", "watch_voice_shared_budget_90"]
            )
            assert await reconcile_incident(restarted, cfg, []) == first
            assert await reconcile_incident(restarted, cfg, []) is None
            async with restarted() as session:
                rows = list((await session.scalars(select(WatchVoiceIncident))).all())
                assert len(rows) == 1 and rows[0].recovered_at is not None
                assert rows[0].log_attempt_count == 3  # initial, escalation, recovery
            messages = [r.message for r in caplog.records if r.name.endswith("watch_voice_monitor")]
            assert sum("Watch Voice fault" in x for x in messages) == 2
            assert sum("Watch Voice recovered" in x for x in messages) == 1

    asyncio.run(scenario())


def test_bad_and_retrying_files_do_not_hold_good_file_and_cursor(tmp_path, monkeypatch):
    world = World()
    world.patches(monkeypatch)
    modified = datetime.now(UTC) - timedelta(seconds=10)
    records = [
        {**world.entry, "id": "id:oversized", "rev": "oversized", "size": 26 * 1024 * 1024},
        {**world.entry, "id": "id:retry", "rev": "retry"},
        {**world.entry, "id": "id:good", "rev": "good"},
    ]
    for record in records:
        record["server_modified"] = modified.isoformat()
    retry_fails = [True]

    def handle(request):
        if "/files/list_folder" in request.url.path:
            return httpx.Response(
                200, json={"entries": records, "cursor": "advanced", "has_more": False}
            )
        if request.url.path.endswith("/files/download"):
            revision = json.loads(request.headers["Dropbox-API-Arg"])["path"]
            if "retry" in revision and retry_fails[0]:
                raise httpx.ConnectError("synthetic offline", request=request)
        response = world.handle(request)
        if request.method == "POST" and request.url.path == "/repos/markmalina/mark/issues":
            issue = world.issues[-1]
            issue["number"] = 100 + world.posts
            issue["html_url"] = f"https://github.com/markmalina/mark/issues/{issue['number']}"
            return httpx.Response(201, json=issue)
        return response

    async def scenario():
        cfg = settings()
        async with database(tmp_path) as factory:
            await admitted_future_cursor(factory, modified - timedelta(seconds=10))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                await worker.process_watch_voice(cfg, factory, http)
                assert world.provider_calls == world.posts == 1
                assert len(await notes(factory)) == 1
                async with factory() as session:
                    sources = list((await session.scalars(select(WatchVoiceSource))).all())
                    states = {s.file_id: s.state for s in sources}
                    assert states == {
                        "id:oversized": "rejected",
                        "id:retry": "retry",
                        "id:good": "archived",
                    }
                    listing = await source_inventory(session, cfg, limit=2)
                    validate(listing, SOURCE_INVENTORY_SCHEMA)
                    assert listing["has_more"]
                    rest = await source_inventory(session, cfg, cursor=listing["next_cursor"])
                    assert listing["count"] + rest["count"] == 3
                    await session.execute(
                        update(WatchVoiceSource)
                        .where(WatchVoiceSource.file_id == "id:retry")
                        .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
                    )
                    await session.commit()
                retry_fails[0] = False
                await worker.process_watch_voice(cfg, factory, http)
                await worker.process_watch_voice(cfg, factory, http)
                assert world.provider_calls == world.posts == 2
                assert all(n.delivery_state == "delivered" for n in await notes(factory))

    asyncio.run(scenario())


def test_monitor_detects_stopped_worker_then_records_recovery_once(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="app.infrastructure.watch_voice_monitor")

    async def scenario():
        cfg = settings().model_copy(update={"deployment_sha": "a" * 40})
        now = datetime.now(UTC)
        async with db(tmp_path) as factory:
            async with factory() as session:
                session.add(
                    WatchVoiceNote(
                        **note(
                            1, account_id="synthetic-owner", received_at=now - timedelta(minutes=20)
                        )
                    )
                )
                await session.commit()
            key = await start_cycle(factory, cfg, driver="persistent")
            async with factory() as session:
                await session.execute(
                    update(WatchVoiceCycle)
                    .where(WatchVoiceCycle.id == key)
                    .values(
                        owns_cursor=True,
                        heartbeat_at=now - timedelta(minutes=5),
                        deadline_at=now - timedelta(minutes=4),
                        scan_finished_at=now - timedelta(minutes=5),
                        finished_at=now - timedelta(minutes=5),
                        outcome="success",
                    )
                )
                await session.commit()
            await observe_watch_voice(cfg, factory)
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                incident = await session.scalar(select(WatchVoiceIncident))
                original_id = incident.id
                assert incident.recovered_at is None and incident.log_attempt_count == 1
                assert "watch_voice_cycle_stale" in incident.codes_json
                await session.execute(
                    update(WatchVoiceCycle)
                    .where(WatchVoiceCycle.id == key)
                    .values(
                        heartbeat_at=now,
                        deadline_at=now + timedelta(seconds=120),
                        scan_finished_at=now,
                        finished_at=now,
                    )
                )
                await session.execute(
                    update(WatchVoiceNote).values(
                        transcription_state="transcribed",
                        delivery_state="delivered",
                        transcript="ready",
                        transcribed_at=now,
                        delivered_at=now,
                        next_part=1,
                        issue_url="https://github.com/markmalina/mark/issues/1",
                    )
                )
                await session.commit()
            await observe_watch_voice(cfg, factory)
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                incident = await session.scalar(select(WatchVoiceIncident))
                assert incident.id == original_id and incident.recovered_at is not None
                assert incident.log_attempt_count == 2
                current = await snapshot(session, cfg)
                assert current["signal_codes"] == []
                assert current["incident"]["recovered_at"] is not None

    asyncio.run(scenario())


def test_shared_budget_includes_other_speech_and_open_reserves(tmp_path):
    async def scenario():
        now = datetime.now(UTC)
        cfg = settings()
        async with db(tmp_path) as factory:
            async with factory() as session:
                workspace_id = uuid.uuid4()
                session.add(
                    Workspace(
                        id=workspace_id, key="mark-personal", display_name="test", is_active=True
                    )
                )
                await session.flush()
                session.add(
                    BillingEntitlementModel(
                        workspace_id=workspace_id,
                        entitlement_key="synthetic",
                        product="gate",
                        scope_kind="telegram_connector",
                        scope_ref="telegram_business",
                        plan_key="test",
                        state="active",
                        valid_from=now - timedelta(days=1),
                        hard_limit_microusd=100,
                    )
                )
                for key, state, amount in (
                    ("watch-voice:test", "settled", 10),
                    ("telegram:other", "settled", 60),
                    ("telegram:held", "unknown", 20),
                ):
                    session.add(
                        BillingBudgetReservationModel(
                            id=uuid.uuid4(),
                            workspace_id=workspace_id,
                            reservation_key=key,
                            product="gate",
                            scope_kind="telegram_connector",
                            scope_ref="telegram_business",
                            operation="transcription",
                            period_key=now.strftime("%Y-%m"),
                            state=state,
                            reserved_microusd=amount,
                            settled_microusd=amount if state == "settled" else None,
                            error_code="synthetic_unknown" if state == "unknown" else None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                await session.commit()
                budget = await shared_budget(session, cfg)
                assert budget["settled_microusd"] == 70
                assert budget["reserved_or_unknown_microusd"] == 20
                assert budget["remaining_microusd"] == 10
                assert budget["basis"] == "shared_speech_not_provider_balance"
            await observe_watch_voice(cfg, factory)
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                incident = await session.scalar(select(WatchVoiceIncident))
                assert json.loads(incident.codes_json) == [
                    "watch_voice_shared_budget_70",
                    "watch_voice_shared_budget_90",
                ]
                assert incident.log_attempt_count == 1

    asyncio.run(scenario())
