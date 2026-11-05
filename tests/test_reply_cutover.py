"""Cutover observations fail closed without reclassifying delivery outcomes."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.api.health_routes import reply_cutover_health
from app.core.config import Settings
from app.infrastructure.reply_cutover import (
    BASELINE_REVISION,
    GUARDED_REVISION,
    ReplyCutoverObservation,
    observe_reply_cutover,
)
from fastapi import FastAPI, HTTPException, Request


@pytest.mark.parametrize("revision", [BASELINE_REVISION, GUARDED_REVISION])
def test_fixed_schema_and_preserved_unknowns_admit_migration(revision: str) -> None:
    observation = ReplyCutoverObservation((revision,), reserved=0, sending=0, unknown=7)
    observation.require_migration_preconditions(sends_enabled=False)
    assert observation.unknown == 7


@pytest.mark.parametrize("reserved,sending", [(1, 0), (0, 1), (1, 1)])
def test_active_attempts_block_migration(reserved: int, sending: int) -> None:
    observation = ReplyCutoverObservation((BASELINE_REVISION,), reserved, sending, unknown=0)
    with pytest.raises(RuntimeError, match="reply_cutover_attempts_not_drained"):
        observation.require_migration_preconditions(sends_enabled=False)


@pytest.mark.parametrize("revisions", [(), ("unreviewed",), (BASELINE_REVISION, "other")])
def test_unknown_schema_or_multiple_heads_block_migration(revisions: tuple[str, ...]) -> None:
    with pytest.raises(RuntimeError, match="reply_cutover_schema_unexpected"):
        ReplyCutoverObservation(revisions, 0, 0, 0).require_migration_preconditions(
            sends_enabled=False,
        )


@pytest.mark.anyio
async def test_observation_only_aggregates_baseline_columns() -> None:
    revisions = MagicMock()
    revisions.scalars.return_value.all.return_value = [BASELINE_REVISION]
    counts = MagicMock()
    counts.all.return_value = [("reserved", 2), ("sending", 3), ("unknown", 5)]
    session = AsyncMock()
    session.execute.side_effect = [revisions, counts]
    observation = await observe_reply_cutover(session)
    assert observation == ReplyCutoverObservation((BASELINE_REVISION,), 2, 3, 5)
    assert observation.active_attempts == 5
    session.commit.assert_not_called()
    queries = [str(call.args[0]) for call in session.execute.call_args_list]
    assert "version_num FROM alembic_version" in queries[0]
    assert "send_attempts.status" in queries[1]
    assert "messages" not in queries[1]
    assert "reply_dialog_versions" not in queries[1]


def _request() -> Request:
    return Request({"type": "http", "app": FastAPI(), "headers": []})


@pytest.mark.anyio
async def test_endpoint_requires_bearer_before_database_access() -> None:
    with pytest.raises(HTTPException) as caught:
        await reply_cutover_health(
            _request(), authorization=None,
            settings=Settings(_env_file=None, ADMIN_BEARER_TOKEN="synthetic-admin"),
        )
    assert caught.value.status_code == 403


@pytest.mark.anyio
async def test_endpoint_database_failure_is_redacted() -> None:
    response = await reply_cutover_health(
        _request(), authorization="Bearer synthetic-admin",
        settings=Settings(_env_file=None, ADMIN_BEARER_TOKEN="synthetic-admin"),
    )
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "unavailable", "reason": "reply_cutover_observation_failed",
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_endpoint_reports_real_baseline_aggregate_without_payloads() -> None:
    from app.infrastructure.database import DatabaseRuntime
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
            await connection.execute(text("CREATE TABLE send_attempts (status TEXT)"))
            await connection.execute(text(
                "INSERT INTO alembic_version VALUES ('0115_watch_voice_post_payment')"
            ))
            await connection.execute(text(
                "INSERT INTO send_attempts VALUES ('sent'), ('unknown'), ('unknown')"
            ))
        request = _request()
        request.app.state.database_runtime = DatabaseRuntime(engine, sessions)
        response = await reply_cutover_health(
            request, authorization="Bearer synthetic-admin",
            settings=Settings(
                _env_file=None, ADMIN_BEARER_TOKEN="synthetic-admin",
                RAILWAY_GIT_COMMIT_SHA="a" * 40, GATE_SEND_ENABLED=False,
            ),
        )
        assert response.status_code == 200
        assert json.loads(response.body) == {
            "status": "observed", "deployment_sha": "a" * 40,
            "schema_revisions": [BASELINE_REVISION], "reserved": 0, "sending": 0,
            "unknown": 2, "send_enabled": False, "guard_contract": "dialog-v1",
        }
        async with sessions() as session:
            counts = await session.execute(text("SELECT COUNT(*) FROM send_attempts"))
            assert counts.scalar_one() == 3
    finally:
        await engine.dispose()


def test_predeploy_uses_only_fixed_migration_and_preserves_unknowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import reply_cutover

    observe = AsyncMock(side_effect=[
        ReplyCutoverObservation((BASELINE_REVISION,), 0, 0, 9),
        ReplyCutoverObservation((GUARDED_REVISION,), 0, 0, 9),
    ])
    upgrade = MagicMock()
    monkeypatch.setattr(reply_cutover, "_observe", observe)
    monkeypatch.setattr(
        reply_cutover, "get_settings",
        lambda: Settings(_env_file=None, GATE_SEND_ENABLED=False),
    )
    monkeypatch.setattr(reply_cutover.command, "upgrade", upgrade)
    reply_cutover.main()
    assert upgrade.call_count == 1
    assert upgrade.call_args.args[1] == GUARDED_REVISION


def test_predeploy_active_attempts_abort_before_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import reply_cutover

    observe = AsyncMock(return_value=ReplyCutoverObservation((BASELINE_REVISION,), 0, 1, 0))
    upgrade = MagicMock()
    monkeypatch.setattr(reply_cutover, "_observe", observe)
    monkeypatch.setattr(
        reply_cutover, "get_settings",
        lambda: Settings(_env_file=None, GATE_SEND_ENABLED=False),
    )
    monkeypatch.setattr(reply_cutover.command, "upgrade", upgrade)
    with pytest.raises(SystemExit) as caught:
        reply_cutover.main()
    assert caught.value.code == 1
    upgrade.assert_not_called()


def test_predeploy_enabled_sends_on_baseline_abort_before_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import reply_cutover

    observe = AsyncMock(return_value=ReplyCutoverObservation((BASELINE_REVISION,), 0, 0, 4))
    upgrade = MagicMock()
    monkeypatch.setattr(reply_cutover, "_observe", observe)
    monkeypatch.setattr(
        reply_cutover, "get_settings",
        lambda: Settings(_env_file=None, GATE_SEND_ENABLED=True),
    )
    monkeypatch.setattr(reply_cutover.command, "upgrade", upgrade)
    with pytest.raises(SystemExit) as caught:
        reply_cutover.main()
    assert caught.value.code == 1
    upgrade.assert_not_called()
    assert observe.await_count == 1


def test_predeploy_enabled_sends_on_guarded_schema_allow_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import reply_cutover

    observation = ReplyCutoverObservation((GUARDED_REVISION,), 0, 0, 4)
    observe = AsyncMock(return_value=observation)
    upgrade = MagicMock()
    monkeypatch.setattr(reply_cutover, "_observe", observe)
    monkeypatch.setattr(
        reply_cutover, "get_settings",
        lambda: Settings(_env_file=None, GATE_SEND_ENABLED=True),
    )
    monkeypatch.setattr(reply_cutover.command, "upgrade", upgrade)
    reply_cutover.main()
    assert upgrade.call_count == 1
    assert upgrade.call_args.args[1] == GUARDED_REVISION
    assert observe.await_count == 2
    assert observation.unknown == 4
