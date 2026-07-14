"""SQLAlchemy and Alembic deployment-readiness adapter."""

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.readiness import (
    CheckReadinessService,
    ReadinessFailureCode,
    ReadinessProbeError,
    ReadinessSnapshot,
)


class ReadinessConfigurationError(RuntimeError):
    """The deployed Alembic repository cannot define readiness."""


def load_expected_migration_heads(config_path: Path | None = None) -> frozenset[str]:
    """Load all Alembic heads shipped in the current application image."""

    root = Path(__file__).resolve().parents[2]
    resolved_config_path = config_path or root / "alembic.ini"
    config = Config(str(resolved_config_path))
    if config_path is None:
        config.set_main_option("script_location", str(root / "alembic"))
    heads = frozenset(ScriptDirectory.from_config(config).get_heads())
    if not heads:
        raise ReadinessConfigurationError("Alembic migration repository has no heads")
    return heads


def _read_current_migration_heads(connection: Connection) -> frozenset[str]:
    """Return every head recorded in the database's Alembic version table."""

    context = MigrationContext.configure(connection)
    return frozenset(context.get_current_heads())


class SqlAlchemyAlembicReadinessProbe:
    """Observe connectivity and migration state through the lifespan engine."""

    def __init__(self, *, engine: AsyncEngine, expected_heads: frozenset[str]) -> None:
        self._engine = engine
        self._expected_heads = expected_heads

    async def inspect(self) -> ReadinessSnapshot:
        """Return current heads or a typed infrastructure failure."""

        try:
            async with self._engine.connect() as connection:
                try:
                    await connection.execute(text("SELECT 1"))
                except (SQLAlchemyError, OSError) as exc:
                    raise ReadinessProbeError(
                        ReadinessFailureCode.DATABASE_UNAVAILABLE
                    ) from exc
                try:
                    current_heads = await connection.run_sync(_read_current_migration_heads)
                except (SQLAlchemyError, OSError) as exc:
                    raise ReadinessProbeError(
                        ReadinessFailureCode.MIGRATION_STATE_UNAVAILABLE
                    ) from exc
        except ReadinessProbeError:
            raise
        except (SQLAlchemyError, OSError) as exc:
            raise ReadinessProbeError(ReadinessFailureCode.DATABASE_UNAVAILABLE) from exc

        return ReadinessSnapshot(
            expected_heads=self._expected_heads,
            current_heads=current_heads,
        )


def build_readiness_service(
    engine: AsyncEngine,
    *,
    config_path: Path | None = None,
    timeout_seconds: float = 2.0,
) -> CheckReadinessService:
    """Compose readiness using the existing lifespan-owned engine."""

    probe = SqlAlchemyAlembicReadinessProbe(
        engine=engine,
        expected_heads=load_expected_migration_heads(config_path),
    )
    return CheckReadinessService(probe, timeout_seconds=timeout_seconds)
