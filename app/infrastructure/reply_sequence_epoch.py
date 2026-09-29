"""Verified fleet epochs and provider-free durable continuity renewal.

Only the protected control executor installs an initial epoch. Runtime may extend
that exact identity, never invent inventory, repair a gap, or reactivate dispatch.
These internal observations cannot prove delivery of every Telegram webhook.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.worker_health import WorkerHealthRegistry
from app.models.domain import TelegramBusinessConnection, TelegramReceipt
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyContinuityEpoch,
    ReplyContinuityRenewal,
    ReplyDispatchControl,
    ReplyIngressProcess,
    ReplyIngressProjection,
    ReplySequence,
)

if TYPE_CHECKING:
    from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime

HEARTBEAT_SECONDS = 120
RENEW_SECONDS = 240
EVIDENCE_SECONDS = 600


class ContinuityDenied(ValueError):
    """Bounded redacted reason; no automatic recovery of old approvals."""


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def config_digest(runtime: ReplySequenceRuntime) -> str:
    settings = runtime.settings
    names = (
        "gate_workspace_id",
        "deployment_sha",
        "gate_send_enabled",
        "gate_reply_sequence_db_pool_mode",
        "gate_reply_sequence_stop_on_inbound_enabled",
        "gate_reply_sequence_dispatch_v2_enabled",
        "gate_reply_sequence_parallel_enabled",
        "gate_reply_sequence_worker_enabled",
        "gate_telegram_worker_enabled",
        "gate_reply_sequence_max_inflight",
        "gate_reply_sequence_max_inflight_per_account",
        "gate_reply_sequence_max_inflight_per_bot",
        "gate_reply_sequence_notifications_enabled",
        "gate_reply_sequence_canary_enabled",
    )
    return digest(
        {name: getattr(settings, name, None) for name in names}
        | {
            "bot_resource": runtime.bot_resource,
        }
    )


async def binding_digest(session: AsyncSession) -> str:
    rows = (
        await session.scalars(
            select(TelegramBusinessConnection).order_by(TelegramBusinessConnection.id)
        )
    ).all()
    # The receipt table and business connector are installation-scoped, as is
    # Gate's existing inbound mapping. Only the hash is retained in the epoch.
    return digest(
        [
            {
                "id": str(row.id),
                "account": str(row.account_id),
                "connection": row.business_connection_id,
                "owner": row.telegram_user_id,
                "route": row.user_chat_id,
                "rights": row.rights,
                "enabled": row.is_enabled,
                "error": bool(row.last_error),
            }
            for row in rows
        ]
    )


async def observe_process(
    runtime: ReplySequenceRuntime,
    registry: WorkerHealthRegistry,
) -> None:
    """Publish completed worker-loop evidence plus DB-backed event-loop liveness."""
    async with runtime.sessions() as session:
        now = await runtime.db_now(session)
        row = await session.get(
            ReplyIngressProcess, (runtime.workspace, runtime.owner), with_for_update=True
        )
        workers: dict[str, dict[str, object]] = {}
        # Registry ages are measured against its own clock, not a remote DB clock.
        snapshot = registry.snapshot()
        for worker in snapshot.workers:
            workers[worker.name] = {
                "enabled": worker.enabled,
                "state": worker.state.value,
                "heartbeat_age": worker.heartbeat_age_seconds,
                "failure_age": worker.last_failure_age_seconds,
            }
        gap = bool(row and (now - row.observed_at).total_seconds() >= HEARTBEAT_SECONDS)
        unhealthy = any(w["enabled"] and w["state"] != "healthy" for w in workers.values())
        if row is None:
            row = ReplyIngressProcess(
                workspace_ref=runtime.workspace, boot_id=runtime.owner, observed_at=now
            )
            session.add(row)
        row.source_sha = runtime.settings.deployment_sha
        row.bot_resource_id = runtime.bot_resource
        row.config_sha256 = config_digest(runtime)
        row.observed_at = now
        row.workers = workers
        if gap or unhealthy:
            row.last_gap_at = now
        await session.commit()


def validate_fleet(
    epoch: ReplyContinuityEpoch,
    processes: list[ReplyIngressProcess],
    now: datetime,
) -> str:
    members = epoch.members
    expected = {member["boot_id"]: member for member in members}
    if not members or len(expected) != len(members):
        raise ContinuityDenied("continuity_inventory_invalid")
    recent = {
        p.boot_id: p for p in processes if (now - p.observed_at).total_seconds() < HEARTBEAT_SECONDS
    }
    if set(recent) != set(expected):
        raise ContinuityDenied("continuity_fleet_changed")
    enabled_workers: set[str] = set()
    for boot, member in expected.items():
        process = recent[boot]
        age = (now - process.observed_at).total_seconds()
        if (
            age < 0
            or process.bot_resource_id != epoch.bot_resource_id
            or process.source_sha != member["source_sha"]
            or (process.config_sha256 != member["config_sha256"])
        ):
            raise ContinuityDenied("continuity_process_changed")
        if process.last_gap_at is not None and process.last_gap_at >= epoch.started_at:
            raise ContinuityDenied("continuity_process_gap")
        if not process.workers:
            raise ContinuityDenied("continuity_worker_evidence_missing")
        for name, worker in process.workers.items():
            if not worker["enabled"]:
                continue
            enabled_workers.add(name)
            heartbeat = worker["heartbeat_age"]
            failure = worker["failure_age"]
            if (
                worker["state"] != "healthy"
                or not isinstance(heartbeat, (int, float))
                or isinstance(heartbeat, bool)
                or not math.isfinite(heartbeat)
                or heartbeat < 0
                or heartbeat + age >= HEARTBEAT_SECONDS
            ):
                raise ContinuityDenied("continuity_worker_unhealthy")
            if isinstance(failure, (int, float)) and (
                process.observed_at - timedelta(seconds=failure) >= epoch.started_at
            ):
                raise ContinuityDenied("continuity_worker_failed")
    if not {"telegram", "reply_sequence"}.issubset(enabled_workers):
        raise ContinuityDenied("continuity_worker_inventory_incomplete")
    return digest(
        [
            {"boot": p.boot_id, "observed": p.observed_at.isoformat(), "workers": p.workers}
            for p in sorted(recent.values(), key=lambda p: p.boot_id)
        ]
    )


async def validate_durable_health(
    session: AsyncSession,
    epoch: ReplyContinuityEpoch,
    now: datetime,
) -> str:
    processes = list(
        (
            await session.scalars(
                select(ReplyIngressProcess).where(
                    ReplyIngressProcess.workspace_ref == epoch.workspace_ref
                )
            )
        ).all()
    )
    observed = validate_fleet(epoch, processes, now)
    if await binding_digest(session) != epoch.binding_sha256:
        raise ContinuityDenied("continuity_binding_changed")
    receipts = select(TelegramReceipt.id).where(
        TelegramReceipt.bot_key == "telegram_business",
        TelegramReceipt.created_at >= epoch.started_at,
    )
    backlog = await session.scalar(
        select(TelegramReceipt.id)
        .where(
            TelegramReceipt.bot_key == "telegram_business",
            TelegramReceipt.status.in_(["received", "processing"]),
            TelegramReceipt.created_at <= now - timedelta(seconds=HEARTBEAT_SECONDS),
        )
        .limit(1)
    )
    if backlog is not None:
        raise ContinuityDenied("continuity_backlog_stale")
    failed = await session.scalar(
        receipts.where(
            or_(
                TelegramReceipt.status == "dead_letter",
                TelegramReceipt.last_error_at.is_not(None),
                (TelegramReceipt.status != "processed")
                & (TelegramReceipt.created_at <= now - timedelta(seconds=HEARTBEAT_SECONDS)),
            )
        ).limit(1)
    )
    if failed is not None:
        raise ContinuityDenied("continuity_receipt_unhealthy")
    unprojected = await session.scalar(
        receipts.where(
            ~select(ReplyIngressProjection.receipt_id)
            .where(
                ReplyIngressProjection.receipt_id == TelegramReceipt.id,
                ReplyIngressProjection.workspace_ref == epoch.workspace_ref,
                ReplyIngressProjection.boot_id.in_([member["boot_id"] for member in epoch.members]),
            )
            .exists()
        ).limit(1)
    )
    if unprojected is not None:
        raise ContinuityDenied("continuity_projection_missing")
    return observed


async def install_verified_epoch(
    session: AsyncSession,
    *,
    workspace: str,
    evidence_sha256: str,
    verified_at: datetime,
    members: list[dict[str, str]],
    binding_sha256: str,
    expected_connector_version: int,
    bot_resource_id: str,
) -> UUID:
    """Protected bootstrap only; verify manifest against actual durable observations.

    The caller is the reviewed Ops control executor and must validate the external
    inventory/provider evidence before invoking this API. An opaque CLI string is
    not sufficient authorization. This operation does not enable dispatch.
    """
    now = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    if (
        verified_at.tzinfo is None
        or not 0 <= (now - verified_at).total_seconds() < 300
        or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", binding_sha256)
    ):
        raise ContinuityDenied("continuity_evidence_invalid")
    if not 1 <= len(members) <= 128:
        raise ContinuityDenied("continuity_inventory_invalid")
    for member in members:
        if (
            set(member) != {"boot_id", "source_sha", "config_sha256"}
            or not re.fullmatch(r"[0-9a-f]{32}", member["boot_id"])
            or not re.fullmatch(r"[0-9a-f]{40}", member["source_sha"])
            or not re.fullmatch(r"[0-9a-f]{64}", member["config_sha256"])
        ):
            raise ContinuityDenied("continuity_inventory_invalid")
    control = await session.get(ReplyDispatchControl, workspace, with_for_update=True)
    connector = await session.scalar(
        select(ReplyConnectorAdmission)
        .where(
            ReplyConnectorAdmission.workspace_ref == workspace,
            ReplyConnectorAdmission.connector_key == "telegram_business",
        )
        .with_for_update()
    )
    if control is None or control.mode != "disabled" or connector is None:
        raise ContinuityDenied("continuity_bootstrap_requires_disabled")
    if connector.version != expected_connector_version:
        raise ContinuityDenied("continuity_version_conflict")
    if (
        await session.scalar(
            select(ReplySequence.id)
            .where(ReplySequence.workspace_ref == workspace, ReplySequence.state == "active")
            .limit(1)
        )
        is not None
    ):
        raise ContinuityDenied("continuity_reapproval_required")
    epoch = ReplyContinuityEpoch(
        id=uuid4(),
        workspace_ref=workspace,
        evidence_sha256=evidence_sha256,
        verified_at=verified_at,
        started_at=verified_at,
        members=members,
        binding_sha256=binding_sha256,
        bot_resource_id=bot_resource_id,
    )
    await validate_durable_health(session, epoch, now)
    session.add(epoch)
    await session.flush()
    connector.epoch_id = epoch.id
    connector.version += 1
    connector.state = "healthy"
    connector.safe_reason = None
    connector.observed_at = now
    connector.continuity_until = now + timedelta(seconds=EVIDENCE_SECONDS)
    return epoch.id


async def renew_verified_epoch(
    session: AsyncSession,
    *,
    connector: ReplyConnectorAdmission,
    now: datetime,
) -> bool:
    """Called under connector/control locks; concurrent losers reread current state.

    No commit here: caller atomically commits either renewal or a terminal fence.
    No dispatch mode, generation, claim, canary deadline or budget is modified.
    """
    if (
        connector.state != "healthy"
        or connector.continuity_until is None
        or now >= connector.continuity_until
    ):
        raise ContinuityDenied("continuity_expired")
    if connector.epoch_id is None:
        raise ContinuityDenied("continuity_epoch_missing")
    epoch = await session.get(ReplyContinuityEpoch, connector.epoch_id)
    if epoch is None or epoch.workspace_ref != connector.workspace_ref:
        raise ContinuityDenied("continuity_epoch_invalid")
    observation_digest = await validate_durable_health(session, epoch, now)
    if (now - connector.observed_at).total_seconds() < RENEW_SECONDS:
        return False
    connector.version += 1
    connector.observed_at = now
    connector.continuity_until = now + timedelta(seconds=EVIDENCE_SECONDS)
    session.add(
        ReplyContinuityRenewal(
            epoch_id=epoch.id,
            connector_version=connector.version,
            observed_at=now,
            continuity_until=connector.continuity_until,
            observation_sha256=observation_digest,
        )
    )
    return True
