"""Application service and contracts for deployment readiness."""

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

logger = logging.getLogger(__name__)


class ReadinessComponentStatus(StrEnum):
    """Public state of one readiness component."""

    OK = "ok"
    FAILED = "failed"
    UNKNOWN = "unknown"
    NOT_CHECKED = "not_checked"


class ReadinessFailureCode(StrEnum):
    """Stable and redacted readiness failure reasons."""

    DATABASE_UNAVAILABLE = "database_unavailable"
    MIGRATION_STATE_UNAVAILABLE = "migration_state_unavailable"
    MIGRATION_HEADS_MISMATCH = "migration_heads_mismatch"
    CHECK_TIMEOUT = "check_timeout"
    CHECK_FAILED = "check_failed"


class ReadinessProbeError(RuntimeError):
    """Expected infrastructure failure with a public-safe reason code."""

    def __init__(self, code: ReadinessFailureCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReadinessSnapshot:
    """Observed code and database migration heads."""

    expected_heads: frozenset[str]
    current_heads: frozenset[str]


class ReadinessProbe(Protocol):
    """Port for one database and migration observation."""

    async def inspect(self) -> ReadinessSnapshot:
        """Return the current redacted snapshot or a typed probe error."""


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Public-safe readiness decision."""

    ready: bool
    database: ReadinessComponentStatus
    migrations: ReadinessComponentStatus
    reason: ReadinessFailureCode | None


class CheckReadinessService:
    """Classify one bounded readiness observation."""

    def __init__(self, probe: ReadinessProbe, *, timeout_seconds: float = 2.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("readiness timeout must be positive")
        self._probe = probe
        self._timeout_seconds = timeout_seconds

    async def execute(self) -> ReadinessResult:
        """Return readiness without retries or sensitive exception details."""

        try:
            async with asyncio.timeout(self._timeout_seconds):
                snapshot = await self._probe.inspect()
        except TimeoutError:
            return ReadinessResult(
                ready=False,
                database=ReadinessComponentStatus.UNKNOWN,
                migrations=ReadinessComponentStatus.UNKNOWN,
                reason=ReadinessFailureCode.CHECK_TIMEOUT,
            )
        except ReadinessProbeError as exc:
            if exc.code is ReadinessFailureCode.DATABASE_UNAVAILABLE:
                return ReadinessResult(
                    ready=False,
                    database=ReadinessComponentStatus.FAILED,
                    migrations=ReadinessComponentStatus.NOT_CHECKED,
                    reason=exc.code,
                )
            if exc.code is ReadinessFailureCode.MIGRATION_STATE_UNAVAILABLE:
                return ReadinessResult(
                    ready=False,
                    database=ReadinessComponentStatus.OK,
                    migrations=ReadinessComponentStatus.FAILED,
                    reason=exc.code,
                )
            logger.warning("Invalid readiness probe error code: code=%s", exc.code.value)
            return ReadinessResult(
                ready=False,
                database=ReadinessComponentStatus.UNKNOWN,
                migrations=ReadinessComponentStatus.UNKNOWN,
                reason=ReadinessFailureCode.CHECK_FAILED,
            )
        except Exception as exc:
            logger.warning(
                "Unexpected readiness failure: error_type=%s",
                exc.__class__.__name__,
            )
            return ReadinessResult(
                ready=False,
                database=ReadinessComponentStatus.UNKNOWN,
                migrations=ReadinessComponentStatus.UNKNOWN,
                reason=ReadinessFailureCode.CHECK_FAILED,
            )

        if snapshot.current_heads != snapshot.expected_heads:
            return ReadinessResult(
                ready=False,
                database=ReadinessComponentStatus.OK,
                migrations=ReadinessComponentStatus.FAILED,
                reason=ReadinessFailureCode.MIGRATION_HEADS_MISMATCH,
            )
        return ReadinessResult(
            ready=True,
            database=ReadinessComponentStatus.OK,
            migrations=ReadinessComponentStatus.OK,
            reason=None,
        )
