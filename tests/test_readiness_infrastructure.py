"""SQLAlchemy and Alembic deployment-readiness adapter tests."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, cast

import pytest
from alembic.migration import MigrationContext
from app.application.readiness import (
    ReadinessFailureCode,
    ReadinessProbeError,
    ReadinessSnapshot,
)
from app.infrastructure import readiness as readiness_module
from app.infrastructure.readiness import (
    ReadinessConfigurationError,
    SqlAlchemyAlembicReadinessProbe,
    _read_current_migration_heads,
    build_readiness_service,
    load_expected_migration_heads,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Connection:
    def __init__(
        self,
        *,
        current_heads: frozenset[str] = frozenset({"head"}),
        execute_error: Exception | None = None,
        run_sync_error: Exception | None = None,
    ) -> None:
        self.current_heads = current_heads
        self.execute_error = execute_error
        self.run_sync_error = run_sync_error
        self.events: list[str] = []

    async def execute(self, _statement: object) -> None:
        self.events.append("execute")
        if self.execute_error is not None:
            raise self.execute_error

    async def run_sync(self, _function: Callable[[Connection], object]) -> frozenset[str]:
        self.events.append("run_sync")
        if self.run_sync_error is not None:
            raise self.run_sync_error
        return self.current_heads


class _ConnectionContext(AbstractAsyncContextManager[AsyncConnection]):
    def __init__(
        self,
        connection: _Connection,
        *,
        enter_error: Exception | None = None,
    ) -> None:
        self.connection = connection
        self.enter_error = enter_error

    async def __aenter__(self) -> AsyncConnection:
        if self.enter_error is not None:
            raise self.enter_error
        return cast(AsyncConnection, self.connection)

    async def __aexit__(self, *args: object) -> None:
        return None


class _Engine:
    def __init__(
        self,
        connection: _Connection,
        *,
        connect_error: Exception | None = None,
    ) -> None:
        self.connection = connection
        self.connect_error = connect_error

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(self.connection, enter_error=self.connect_error)


def _probe(
    connection: _Connection,
    *,
    connect_error: Exception | None = None,
    expected_heads: frozenset[str] = frozenset({"head"}),
) -> SqlAlchemyAlembicReadinessProbe:
    return SqlAlchemyAlembicReadinessProbe(
        engine=cast(AsyncEngine, _Engine(connection, connect_error=connect_error)),
        expected_heads=expected_heads,
    )


@pytest.mark.anyio
async def test_probe_pings_database_before_reading_all_migration_heads() -> None:
    connection = _Connection(current_heads=frozenset({"head_a", "head_b"}))
    probe = _probe(
        connection,
        expected_heads=frozenset({"head_b", "head_a"}),
    )

    snapshot = await probe.inspect()

    assert snapshot == ReadinessSnapshot(
        expected_heads=frozenset({"head_a", "head_b"}),
        current_heads=frozenset({"head_a", "head_b"}),
    )
    assert connection.events == ["execute", "run_sync"]


@pytest.mark.anyio
@pytest.mark.parametrize("at_connect", [True, False])
async def test_probe_classifies_connect_and_ping_failures_as_database_unavailable(
    at_connect: bool,
) -> None:
    error = SQLAlchemyError("secret database detail")
    connection = _Connection(execute_error=None if at_connect else error)
    probe = _probe(connection, connect_error=error if at_connect else None)

    with pytest.raises(ReadinessProbeError) as captured:
        await probe.inspect()

    assert captured.value.code is ReadinessFailureCode.DATABASE_UNAVAILABLE
    assert "secret database detail" not in str(captured.value)


@pytest.mark.anyio
async def test_probe_classifies_alembic_read_failure_after_successful_ping() -> None:
    connection = _Connection(run_sync_error=SQLAlchemyError("secret migration detail"))

    with pytest.raises(ReadinessProbeError) as captured:
        await _probe(connection).inspect()

    assert captured.value.code is ReadinessFailureCode.MIGRATION_STATE_UNAVAILABLE
    assert connection.events == ["execute", "run_sync"]
    assert "secret migration detail" not in str(captured.value)


@pytest.mark.anyio
async def test_probe_does_not_mask_programming_errors() -> None:
    connection = _Connection(run_sync_error=ValueError("programming error"))

    with pytest.raises(ValueError, match="programming error"):
        await _probe(connection).inspect()


def test_current_heads_reader_uses_plural_alembic_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Context:
        def get_current_heads(self) -> tuple[str, ...]:
            return ("head_a", "head_b")

    configured_with: list[Connection] = []

    def fake_configure(connection: Connection) -> _Context:
        configured_with.append(connection)
        return _Context()

    connection = cast(Connection, object())
    monkeypatch.setattr(MigrationContext, "configure", fake_configure)

    assert _read_current_migration_heads(connection) == frozenset({"head_a", "head_b"})
    assert configured_with == [connection]


def _write_revision(path: Path, revision: str) -> None:
    path.write_text(
        "\n".join(
            [
                f'revision = "{revision}"',
                "down_revision = None",
                "branch_labels = None",
                "depends_on = None",
            ]
        ),
        encoding="utf-8",
    )


def _alembic_config(tmp_path: Path, *revisions: str) -> Path:
    script_location = tmp_path / "alembic"
    versions = script_location / "versions"
    versions.mkdir(parents=True)
    for revision in revisions:
        _write_revision(versions / f"{revision}.py", revision)
    config_path = tmp_path / "alembic.ini"
    config_path.write_text(
        f"[alembic]\nscript_location = {script_location}\n",
        encoding="utf-8",
    )
    return config_path


def test_expected_migration_heads_loads_every_repository_head(tmp_path: Path) -> None:
    config_path = _alembic_config(tmp_path, "head_a", "head_b")

    assert load_expected_migration_heads(config_path) == frozenset({"head_a", "head_b"})


def test_expected_migration_heads_rejects_empty_repository(tmp_path: Path) -> None:
    config_path = _alembic_config(tmp_path)

    with pytest.raises(ReadinessConfigurationError, match="no heads"):
        load_expected_migration_heads(config_path)


def test_builder_uses_exact_supplied_engine_and_expected_heads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = cast(AsyncEngine, object())
    expected_heads = frozenset({"head_a", "head_b"})
    captured: dict[str, Any] = {}

    class _CapturingProbe:
        def __init__(self, *, engine: AsyncEngine, expected_heads: frozenset[str]) -> None:
            captured["engine"] = engine
            captured["expected_heads"] = expected_heads

        async def inspect(self) -> ReadinessSnapshot:
            return ReadinessSnapshot(
                expected_heads=expected_heads,
                current_heads=expected_heads,
            )

    monkeypatch.setattr(
        readiness_module,
        "load_expected_migration_heads",
        lambda _config_path: expected_heads,
    )
    monkeypatch.setattr(
        readiness_module,
        "SqlAlchemyAlembicReadinessProbe",
        _CapturingProbe,
    )

    service = build_readiness_service(
        engine,
        config_path=Path("custom.ini"),
        timeout_seconds=7.0,
    )

    assert captured == {"engine": engine, "expected_heads": expected_heads}
    assert service._timeout_seconds == 7.0
