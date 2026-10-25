import asyncio
import uuid
from datetime import UTC, datetime

from app.core.config import Settings
from app.infrastructure.watch_voice_metrics import (
    METRICS_SCHEMA,
    OBSERVATION,
    distribution,
    observed_cycle,
    signals,
    snapshot,
)
from app.models.billing import BillingBudgetReservationModel as Reservation
from app.models.canonical import Workspace
from app.models.watch_voice import WatchVoiceNote
from jsonschema import validate
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.test_watch_voice_inventory import note


def test_percentile_sample_size_and_no_negative_clock_samples():
    result = distribution([-1, float("nan"), 1, 2, 3, 4, 5])
    assert result["n"] == 5 and result["p50_seconds"] == 3
    assert result["p95_seconds"] == 5 and result["preliminary"]
    assert distribution([])["p95_seconds"] is None
    assert not distribution(list(range(30)))["preliminary"]


def test_idle_and_legacy_actions_do_not_raise_persistent_worker_alarm():
    args = dict(oldest_age=1000, progress_age=1000, cycle_age=1000, scan_age=0, unknown=0)
    assert signals(expected="persistent", queue=0, **args) == []
    assert signals(expected="actions", queue=12, **args) == []
    assert signals(expected="persistent", queue=12, **args) == [
        "watch_voice_cycle_stale", "watch_voice_queue_no_progress"]
    args.update(progress_age=None, unknown=1)
    assert "watch_voice_reconciliation_required" in signals(
        expected="persistent", queue=1, **args)


def test_error_telemetry_never_copies_provider_exception_text():
    @observed_cycle
    async def cycle():
        raise RuntimeError("synthetic-secret https://example.invalid/private")
    try:
        asyncio.run(cycle())
    except RuntimeError:
        pass
    assert OBSERVATION.error_code == "watch_voice_cycle_failed"
    @observed_cycle
    async def recovered():
        return 3
    assert asyncio.run(recovered()) == 3
    assert OBSERVATION.error_code is None and OBSERVATION.completed_at is not None


def test_metrics_join_only_exact_owner_voice_charges_and_exclude_audio(tmp_path):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "metrics.db"))
        async with engine.begin() as conn:
            for table in (Workspace.__table__, WatchVoiceNote.__table__, Reservation.__table__):
                await conn.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        workspace = uuid.uuid4()
        now = datetime.now(UTC)
        async with factory() as session:
            session.add(Workspace(id=workspace, key="mark-personal", display_name="synthetic"))
            session.add(WatchVoiceNote(**note(1)))
            for index, key in enumerate((f"watch-voice:{uuid.UUID(int=1)}", "telegram:foreign")):
                session.add(Reservation(id=uuid.uuid4(), workspace_id=workspace,
                    reservation_key=key, product="gate", scope_kind="telegram_connector",
                    scope_ref="telegram_business", operation="transcription",
                    period_key=now.strftime("%Y-%m"), state="settled", reserved_microusd=100,
                    settled_microusd=10 + index, created_at=now, updated_at=now))
            await session.commit()
        async with factory() as session:
            result = await snapshot(session, Settings(DROPBOX_CALL_ACCOUNT_ID="owner"))
            validate(result, METRICS_SCHEMA)
            assert result["voice_cost"]["settled_microusd"] == 10
            assert result["voice_cost"]["reservation_count"] == 1
            assert result["queue_size"] == 1
            assert result["running_driver_proven"] is False
            assert result["latency"]["phases"]["provider_request"]["n"] == 0
            assert "synthetic.m4a" not in str(result)
        await engine.dispose()
    asyncio.run(scenario())
