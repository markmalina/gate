"""Fail-closed fleet/worker predicates without provider or database connections."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.infrastructure.reply_sequence_epoch import (
    ContinuityDenied,
    validate_fleet,
)
from app.models.reply_sequences import ReplyContinuityEpoch, ReplyIngressProcess

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def evidence():
    process = ReplyIngressProcess(
        workspace_ref="synthetic",
        boot_id="a" * 32,
        source_sha="b" * 40,
        config_sha256="c" * 64,
        bot_resource_id="e" * 64,
        observed_at=NOW,
        last_gap_at=None,
        workers={
            name: {"enabled": True, "state": "healthy", "heartbeat_age": 1, "failure_age": None}
            for name in ("telegram", "reply_sequence")
        },
    )
    epoch = ReplyContinuityEpoch(
        id=uuid4(),
        workspace_ref="synthetic",
        evidence_sha256="d" * 64,
        binding_sha256="f" * 64,
        bot_resource_id="e" * 64,
        started_at=NOW - timedelta(seconds=30),
        verified_at=NOW - timedelta(seconds=30),
        members=[
            {
                "boot_id": process.boot_id,
                "source_sha": process.source_sha,
                "config_sha256": process.config_sha256,
            }
        ],
    )
    return epoch, process


def test_complete_fresh_fleet_has_digest_not_payload():
    epoch, process = evidence()
    result = validate_fleet(epoch, [process], NOW)
    assert len(result) == 64


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "extra",
        "duplicate",
        "restart",
        "source",
        "config",
        "bot",
        "stale",
        "future",
        "gap",
        "workers_missing",
        "worker_stale",
        "worker_failed",
        "worker_degraded",
        "missing_projector",
    ],
)
def test_invalid_fleet_never_counts_as_healthy(kind):
    epoch, process = evidence()
    processes = [process]
    if kind == "missing":
        processes = []
    elif kind == "extra":
        _, extra = evidence()
        extra.boot_id = "2" * 32
        processes.append(extra)
    elif kind == "duplicate":
        epoch.members = epoch.members * 2
    elif kind == "restart":
        process.boot_id = "2" * 32
    elif kind == "source":
        process.source_sha = "2" * 40
    elif kind == "config":
        process.config_sha256 = "2" * 64
    elif kind == "bot":
        process.bot_resource_id = "2" * 64
    elif kind == "stale":
        process.observed_at = NOW - timedelta(seconds=120)
    elif kind == "future":
        process.observed_at = NOW + timedelta(seconds=1)
    elif kind == "gap":
        process.last_gap_at = NOW - timedelta(seconds=1)
    elif kind == "workers_missing":
        process.workers = {}
    elif kind == "worker_stale":
        process.workers["telegram"]["heartbeat_age"] = 120
    elif kind == "worker_failed":
        process.workers["telegram"]["failure_age"] = 1
    elif kind == "worker_degraded":
        process.workers["telegram"]["state"] = "degraded"
    elif kind == "missing_projector":
        process.workers.pop("telegram")
    with pytest.raises(ContinuityDenied):
        validate_fleet(epoch, processes, NOW)


def test_pre_epoch_worker_error_does_not_block_new_explicit_approval():
    epoch, process = evidence()
    process.workers["telegram"]["failure_age"] = 60
    process.last_gap_at = NOW - timedelta(seconds=60)
    assert validate_fleet(epoch, [process], NOW)
