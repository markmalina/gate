"""Durable incident deduplication on existing Gate logs/status; no new sender or loop."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.billing import BillingBudgetReservationModel, BillingEntitlementModel
from app.models.canonical import Workspace
from app.models.watch_voice import WatchVoiceIncident

logger = logging.getLogger(__name__)


async def shared_budget(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    now = datetime.now(UTC)
    workspace = await session.scalar(
        select(Workspace.id).where(
            Workspace.key == settings.gate_workspace_id, Workspace.is_active.is_(True)
        )
    )
    period = now.strftime("%Y-%m")
    result = {
        "period": period,
        "hard_limit_microusd": None,
        "settled_microusd": None,
        "reserved_or_unknown_microusd": None,
        "remaining_microusd": None,
        "basis": "shared_speech_not_provider_balance",
    }
    if workspace is None:
        return result
    scopes = (
        BillingEntitlementModel.workspace_id == workspace,
        BillingEntitlementModel.product == "gate",
        BillingEntitlementModel.scope_kind == "telegram_connector",
        BillingEntitlementModel.scope_ref == "telegram_business",
        BillingEntitlementModel.state == "active",
        BillingEntitlementModel.valid_from <= now,
        or_(
            BillingEntitlementModel.valid_until.is_(None), BillingEntitlementModel.valid_until > now
        ),
    )
    entitlements = list(
        (await session.scalars(select(BillingEntitlementModel).where(*scopes))).all()
    )
    if len(entitlements) != 1 or entitlements[0].hard_limit_microusd is None:
        return result
    hard = entitlements[0].hard_limit_microusd
    rows = list(
        (
            await session.execute(
                select(
                    BillingBudgetReservationModel.state,
                    BillingBudgetReservationModel.reserved_microusd,
                    BillingBudgetReservationModel.settled_microusd,
                ).where(
                    BillingBudgetReservationModel.workspace_id == workspace,
                    BillingBudgetReservationModel.product == "gate",
                    BillingBudgetReservationModel.scope_kind == "telegram_connector",
                    BillingBudgetReservationModel.scope_ref == "telegram_business",
                    BillingBudgetReservationModel.period_key == period,
                )
            )
        ).all()
    )
    spent = sum(r[2] or 0 for r in rows if r[0] == "settled")
    held = sum(r[1] for r in rows if r[0] in ("reserved", "unknown"))
    return {
        **result,
        "hard_limit_microusd": hard,
        "settled_microusd": spent,
        "reserved_or_unknown_microusd": held,
        "remaining_microusd": max(0, hard - spent - held),
    }


async def reconcile_incident(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    codes: list[str],
    *,
    now: datetime | None = None,
) -> str | None:
    observed = now or datetime.now(UTC)
    codes = sorted(set(codes))
    log_event: tuple[str, str, list[str]] | None = None
    async with factory() as session:
        try:
            if session.get_bind().dialect.name == "postgresql":
                digest = hashlib.sha256(
                    (
                        "gate.watch_voice_incidents|"
                        + settings.gate_workspace_id
                        + "|"
                        + settings.dropbox_call_account_id
                    ).encode()
                ).digest()
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": int.from_bytes(digest[:8], "big", signed=True)},
                )
            row = await session.scalar(
                select(WatchVoiceIncident)
                .where(
                    WatchVoiceIncident.workspace_key == settings.gate_workspace_id,
                    WatchVoiceIncident.account_id == settings.dropbox_call_account_id,
                    WatchVoiceIncident.recovered_at.is_(None),
                )
                .with_for_update()
            )
            if row is None and codes:
                row = WatchVoiceIncident(
                    id=str(uuid.uuid4()),
                    workspace_key=settings.gate_workspace_id,
                    account_id=settings.dropbox_call_account_id,
                    codes_json="[]",
                    attempted_codes_json="[]",
                    opened_at=observed,
                    observed_at=observed,
                    log_attempt_count=0,
                )
                session.add(row)
            if row is None:
                return None
            row.observed_at = observed
            if not codes:
                row.recovered_at = observed
                row.codes_json = "[]"
                row.log_attempt_count += 1
                log_event = ("recovered", row.id, [])
            else:
                attempted = set(json.loads(row.attempted_codes_json))
                new = sorted(set(codes) - attempted)
                row.codes_json = json.dumps(codes)
                if new:
                    row.attempted_codes_json = json.dumps(sorted(attempted | set(new)))
                    row.log_attempt_count += 1
                    log_event = ("fault", row.id, new)
            key = row.id
            # Consume the log attempt before I/O. A crash cannot duplicate it;
            # status retains the incident even if the best-effort log was lost.
            await session.commit()
        except IntegrityError:
            await session.rollback()  # another observer won the unique open-incident race
            return None
    if log_event:
        event, key, new_codes = log_event
        if event == "fault":
            logger.warning("Watch Voice fault: incident=%s codes=%s", key, ",".join(new_codes))
        else:
            logger.info("Watch Voice recovered: incident=%s", key)
    return key


async def observe_watch_voice(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    if not (settings.gate_watch_voice_enabled or settings.gate_watch_voice_actions_enabled):
        return
    from app.infrastructure.watch_voice_metrics import snapshot

    async with factory() as session:
        metrics = await snapshot(session, settings)
        budget = await shared_budget(session, settings)
    codes = list(metrics["signal_codes"])
    hard, remaining = budget["hard_limit_microusd"], budget["remaining_microusd"]
    if hard is not None and remaining is not None:
        consumed = hard - remaining
        for percent in (70, 90, 100):
            if consumed * 100 >= hard * percent:
                codes.append(f"watch_voice_shared_budget_{percent}")
    if (
        not codes
        and metrics["expected_driver"] in {"persistent", "actions"}
        and not metrics["running_driver_proven"]
    ):
        # Startup grace is not recovery. Preserve an existing incident until
        # the expected cursor owner supplies positive, current-source evidence.
        return
    await reconcile_incident(factory, settings, codes)
