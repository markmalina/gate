import asyncio
from datetime import UTC, datetime, timedelta

from app.models.watch_voice import WatchEarlyCard, WatchEarlyRejection, WatchScheduleControl
from app.worker.watch_voice_observe import observe
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_snapshot_counts_age_and_hides_source_data():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for table in (
                WatchEarlyCard.__table__,
                WatchEarlyRejection.__table__,
                WatchScheduleControl.__table__,
            ):
                await conn.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime(2026, 11, 14, 12, tzinfo=UTC)
        async with factory() as session:
            for n, state in enumerate(("pending", "pending", "unknown")):
                session.add(
                    WatchEarlyCard(
                        id=str(n),
                        workspace_key="private",
                        account_id="private",
                        file_id=str(n),
                        revision="r",
                        detected_at=now - timedelta(minutes=n + 1),
                        source_modified_at=now,
                        body="secret content",
                        title="secret title",
                        state=state,
                    )
                )
            session.add(
                WatchEarlyRejection(
                    workspace_key="private",
                    account_id="private",
                    metadata_sha256="x" * 64,
                    reason="bad",
                    detected_at=now,
                )
            )
            session.add(
                WatchScheduleControl(
                    name="private-mark-watch",
                    state="unknown",
                    last_slot=1,
                    last_outcome="unknown",
                    changed_at=now,
                )
            )
            await session.commit()
        snapshot = await observe(factory, now)
        assert snapshot["states"]["pending"] == {"count": 2, "oldest_detected_seconds": 120}
        assert snapshot["states"]["unknown"]["count"] == 1
        assert snapshot["rejections_total"] == 1
        assert snapshot["scheduler_state"] == "unknown"
        assert "secret" not in str(snapshot) and "private" not in str(snapshot)
        await engine.dispose()

    asyncio.run(scenario())
