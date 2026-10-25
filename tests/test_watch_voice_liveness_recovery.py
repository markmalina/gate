"""Regression769: actual SQL lifecycle evidence, no network or provider effects."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from app.infrastructure.watch_voice_journal import (
    CURRENT_CYCLE,
    expect_worker,
    finish_cycle,
    pulse,
    start_cycle,
)
from app.infrastructure.watch_voice_metrics import METRICS_SCHEMA, snapshot
from app.infrastructure.watch_voice_monitor import observe_watch_voice, reconcile_incident
from app.models.watch_voice import (
    WatchVoiceCycle,
    WatchVoiceIncident,
    WatchVoiceNote,
    WatchVoiceSource,
)
from jsonschema import validate
from sqlalchemy import select, update
from tests.test_watch_voice import settings
from tests.test_watch_voice_durable import db
from tests.test_watch_voice_inventory import note


def source(now, **changes):
    return WatchVoiceSource(
        **{
            "id": str(uuid.UUID(int=1)),
            "workspace_key": "mark-personal",
            "account_id": "synthetic-owner",
            "file_id": "id:synthetic",
            "revision": "synthetic",
            "filename": "synthetic.m4a",
            "source_path": "/Alpha/Voice/synthetic.m4a",
            "size_bytes": 1,
            "dropbox_modified_at": now,
            "detected_at": now,
            "state": "downloading",
            "attempt_count": 1,
            "claim_id": "synthetic-claim",
            "lease_expires_at": now,
            **changes,
        }
    )


async def successful_idle(factory, cfg):
    key = await start_cycle(factory, cfg, driver="persistent")
    token = CURRENT_CYCLE.set((factory, key))
    try:
        await pulse("dropbox_scanned", owns_cursor=True, scan_finished_at=datetime.now(UTC))
        await finish_cycle("success")
    finally:
        CURRENT_CYCLE.reset(token)


def test_cancelled_first_download_is_queued_and_cannot_recover_incident(tmp_path):
    async def scenario():
        cfg = settings().model_copy(update={"deployment_sha": "a" * 40})
        old = datetime.now(UTC) - timedelta(hours=1)
        async with db(tmp_path) as factory:
            original = await reconcile_incident(factory, cfg, ["watch_voice_cycle_stale"])
            key = await start_cycle(factory, cfg, driver="persistent")
            token = CURRENT_CYCLE.set((factory, key))
            try:
                await pulse("source_download", owns_cursor=True)
                await finish_cycle("cancelled", "watch_voice_cycle_cancelled")
            finally:
                CURRENT_CYCLE.reset(token)
            async with factory() as session:
                session.add(source(old))
                await session.execute(
                    update(WatchVoiceCycle).values(
                        started_at=old, heartbeat_at=old, finished_at=old, deadline_at=old
                    )
                )
                await session.commit()
                observed = await snapshot(session, cfg)
                validate(observed, METRICS_SCHEMA)
                assert observed["queue_size"] == observed["source_queue_size"] == 1
                assert observed["archived_queue_size"] == 0
                assert observed["oldest_waiting_age_seconds"] >= 3600
                assert not observed["running_driver_proven"]
                assert {
                    "watch_voice_worker_unobserved",
                    "watch_voice_cycle_cancelled",
                    "watch_voice_source_lease_expired",
                    "watch_voice_queue_no_progress",
                } <= set(observed["signal_codes"])
            await observe_watch_voice(cfg, factory)
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                incident = await session.get(WatchVoiceIncident, original)
                assert incident.recovered_at is None
                assert incident.log_attempt_count == 2  # initial fault, new diagnostic codes
                await session.execute(
                    update(WatchVoiceSource).values(
                        state="archived",
                        claim_id=None,
                        lease_expires_at=None,
                        archived_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            await successful_idle(factory, cfg)
            await observe_watch_voice(cfg, factory)
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                incident = await session.get(WatchVoiceIncident, original)
                assert incident.recovered_at is not None and incident.log_attempt_count == 3
                result = await snapshot(session, cfg)
                assert result["running_driver_proven"] and result["signal_codes"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("driver,sha", [("actions", "a" * 40), ("persistent", "b" * 40)])
def test_wrong_driver_or_revision_cannot_hide_backlog(tmp_path, driver, sha):
    async def scenario():
        cfg = settings().model_copy(update={"deployment_sha": "a" * 40})
        old = datetime.now(UTC) - timedelta(hours=1)
        async with db(tmp_path) as factory:
            original = await reconcile_incident(factory, cfg, ["watch_voice_cycle_stale"])
            await start_cycle(factory, cfg, driver=driver)
            async with factory() as session:
                session.add(
                    WatchVoiceNote(
                        **note(1, account_id=cfg.dropbox_call_account_id, received_at=old)
                    )
                )
                await session.execute(
                    update(WatchVoiceCycle).values(
                        owns_cursor=True,
                        source_sha=sha,
                        phase="normalizing",
                        deadline_at=datetime.now(UTC) + timedelta(minutes=20),
                        scan_finished_at=datetime.now(UTC),
                    )
                )
                await session.commit()
                result = await snapshot(session, cfg)
                assert not result["running_driver_proven"]
                assert result["last_successful_scan_at"] is None
                assert "watch_voice_worker_unobserved" in result["signal_codes"]
                assert "watch_voice_queue_no_progress" in result["signal_codes"]
                assert (
                    "watch_voice_driver_mismatch"
                    if driver == "actions"
                    else "watch_voice_source_mismatch"
                ) in result["signal_codes"]
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                assert (await session.get(WatchVoiceIncident, original)).recovered_at is None

    asyncio.run(scenario())


def test_no_cycle_fault_bounded_startup_grace_and_positive_idle_recovery(tmp_path):
    async def scenario():
        cfg = settings().model_copy(update={"deployment_sha": "a" * 40})
        async with db(tmp_path) as factory:
            async with factory() as session:
                missing = await snapshot(session, cfg)
                assert missing["queue_size"] == 0
                assert "watch_voice_worker_unobserved" in missing["signal_codes"]
            await observe_watch_voice(cfg, factory)
            await expect_worker(factory, cfg)
            async with factory() as session:
                starting = await snapshot(session, cfg)
                validate(starting, METRICS_SCHEMA)
                assert starting["liveness_state"] == "starting"
                assert not starting["running_driver_proven"] and starting["signal_codes"] == []
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                incident = await session.scalar(select(WatchVoiceIncident))
                assert incident.recovered_at is None  # empty codes during grace are not recovery
                await session.execute(
                    update(WatchVoiceCycle).values(
                        deadline_at=datetime.now(UTC) - timedelta(seconds=1)
                    )
                )
                await session.commit()
                expired = await snapshot(session, cfg)
                assert "watch_voice_phase_deadline_exceeded" in expired["signal_codes"]
                assert "watch_voice_worker_unobserved" in expired["signal_codes"]
            await observe_watch_voice(cfg, factory)
            await successful_idle(factory, cfg)
            await observe_watch_voice(cfg, factory)
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                result = await snapshot(session, cfg)
                assert result["liveness_state"] == "running"
                assert result["queue_size"] == 0 and result["signal_codes"] == []
                assert result["incident"]["recovered_at"] is not None

    asyncio.run(scenario())


def test_archive_ack_loss_does_not_count_source_twice(tmp_path):
    async def scenario():
        cfg = settings().model_copy(update={"deployment_sha": "a" * 40})
        old = datetime.now(UTC) - timedelta(hours=1)
        async with db(tmp_path) as factory:
            async with factory() as session:
                session.add(source(old))
                session.add(WatchVoiceNote(**note(1, account_id=cfg.dropbox_call_account_id)))
                await session.commit()
                result = await snapshot(session, cfg)
                assert result["queue_size"] == result["archived_queue_size"] == 1
                assert result["source_queue_size"] == 0
                assert "watch_voice_source_lease_expired" in result["signal_codes"]

    asyncio.run(scenario())


def test_actions_idle_does_not_raise_alarm_or_falsely_recover_old_incident(tmp_path):
    async def scenario():
        cfg = settings().model_copy(
            update={
                "deployment_sha": "a" * 40,
                "gate_watch_voice_enabled": False,
                "gate_watch_voice_actions_enabled": True,
            }
        )
        async with db(tmp_path) as factory:
            original = await reconcile_incident(factory, cfg, ["watch_voice_cycle_stale"])
            async with factory() as session:
                idle = await snapshot(session, cfg)
                assert idle["signal_codes"] == [] and not idle["running_driver_proven"]
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                assert (await session.get(WatchVoiceIncident, original)).recovered_at is None
            key = await start_cycle(factory, cfg, driver="actions")
            token = CURRENT_CYCLE.set((factory, key))
            try:
                await pulse("dropbox_scanned", owns_cursor=True, scan_finished_at=datetime.now(UTC))
                await finish_cycle("success")
            finally:
                CURRENT_CYCLE.reset(token)
            await observe_watch_voice(cfg, factory)
            async with factory() as session:
                assert (await session.get(WatchVoiceIncident, original)).recovered_at is not None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"gate_watch_voice_enabled": False},
        {"gate_watch_voice_actions_enabled": True},
        {"gate_watch_voice_data_gate_approved": False},
        {"gate_watch_voice_workspace_key": "foreign"},
    ],
)
def test_startup_expectation_does_not_write_outside_scope(change):
    factory = AsyncMock()
    asyncio.run(expect_worker(factory, settings().model_copy(update=change)))
    factory.assert_not_called()
