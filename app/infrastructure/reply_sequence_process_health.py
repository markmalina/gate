"""Read-only G2 admission check for the startup-selected 0115 primary bot.

A completed bootstrap observation never authorizes an HTTP write. Every check
binds this loaded process to the current protected epoch and its complete fleet.
The caller maps ContinuityDenied to its local no-HTTP transport fence outcome.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, text

from app.application.reply_sequence_policy import text_digest
from app.infrastructure.reply_sequence_epoch import (
    ContinuityDenied,
    config_digest,
    validate_durable_health,
)
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyContinuityEpoch,
    ReplyDispatchControl,
    ReplyIngressProcess,
)

if TYPE_CHECKING:
    from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime


async def require_verified_process_health(
    runtime: ReplySequenceRuntime,
    *,
    bot_resource_id: str,
) -> None:
    """Deny unless the actual primary bot and this loaded process have valid G2 health.

    Does not initialize schema, create observations, renew evidence, arm admission,
    alter mode, or call a provider. Missing schema and DB errors remain local denials.
    """
    prefix = runtime.settings.telegram_bot_token.split(":", 1)[0]
    if (
        not prefix.isdecimal()
        or int(prefix) <= 0
        or bot_resource_id != text_digest("telegram-bot:" + prefix)
        or bot_resource_id != runtime.bot_resource
    ):
        raise ContinuityDenied("continuity_primary_bot_mismatch")
    if (
        runtime.workspace != runtime.settings.gate_workspace_id
        or not re.fullmatch(r"[0-9a-f]{40}", runtime.settings.deployment_sha)
        or not runtime.settings.gate_reply_sequence_stop_on_inbound_enabled
        or not runtime.settings.gate_reply_sequence_dispatch_v2_enabled
    ):
        raise ContinuityDenied("continuity_loaded_process_invalid")
    try:
        async with runtime.sessions() as session:
            # Enforce the callback's read-only contract at the database boundary.
            await session.execute(text("SET TRANSACTION READ ONLY"))
            revisions = list(
                (await session.scalars(text("SELECT version_num FROM alembic_version"))).all()
            )
            if revisions != ["0115_reply_canary_binding"]:
                raise ContinuityDenied("continuity_process_schema_mismatch")
            now = await session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ContinuityDenied("continuity_process_clock_unavailable")
            # These checks deliberately use plain SELECTs: SELECT FOR SHARE is
            # unavailable in a read-only transaction. HTTP remains separately fenced.
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            if (
                control is None
                or not control.migration_complete
                or control.mode
                not in {
                    "serial",
                    "parallel",
                }
            ):
                raise ContinuityDenied("continuity_process_dispatch_closed")
            if (
                control.max_inflight != runtime.settings.gate_reply_sequence_max_inflight
                or control.account_cap
                != runtime.settings.gate_reply_sequence_max_inflight_per_account
                or control.bot_cap != runtime.settings.gate_reply_sequence_max_inflight_per_bot
                or (
                    control.mode == "parallel"
                    and not runtime.settings.gate_reply_sequence_parallel_enabled
                )
            ):
                raise ContinuityDenied("continuity_loaded_process_invalid")
            connector = await session.scalar(
                select(ReplyConnectorAdmission).where(
                    ReplyConnectorAdmission.workspace_ref == runtime.workspace,
                    ReplyConnectorAdmission.connector_key == "telegram_business",
                )
            )
            if (
                connector is None
                or connector.epoch_id is None
                or connector.state != "healthy"
                or connector.continuity_until is None
                or connector.continuity_until <= now
            ):
                raise ContinuityDenied("continuity_process_admission_closed")
            epoch = await session.get(ReplyContinuityEpoch, connector.epoch_id)
            if (
                epoch is None
                or epoch.workspace_ref != runtime.workspace
                or (epoch.bot_resource_id != bot_resource_id)
            ):
                raise ContinuityDenied("continuity_process_epoch_invalid")
            member = next((m for m in epoch.members if m["boot_id"] == runtime.owner), None)
            loaded_config = config_digest(runtime)
            if (
                member is None
                or member["source_sha"] != runtime.settings.deployment_sha
                or (member["config_sha256"] != loaded_config)
            ):
                raise ContinuityDenied("continuity_process_not_verified")
            observed = await session.get(ReplyIngressProcess, (runtime.workspace, runtime.owner))
            if (
                observed is None
                or observed.bot_resource_id != bot_resource_id
                or observed.source_sha != runtime.settings.deployment_sha
                or observed.config_sha256 != loaded_config
            ):
                raise ContinuityDenied("continuity_process_observation_mismatch")
            await validate_durable_health(session, epoch, now)
            # Recheck the mutable admission after the fleet/receipt reads. A
            # healthy renewal may change connector.version; mode/generation and
            # epoch changes, a stop, or an expiry during the scan must deny.
            final = (
                await session.execute(
                    select(
                        ReplyDispatchControl.mode,
                        ReplyDispatchControl.generation,
                        ReplyDispatchControl.migration_complete,
                        ReplyConnectorAdmission.state,
                        ReplyConnectorAdmission.epoch_id,
                        ReplyConnectorAdmission.continuity_until,
                        func.clock_timestamp(),
                    )
                    .join(
                        ReplyConnectorAdmission,
                        ReplyConnectorAdmission.workspace_ref == ReplyDispatchControl.workspace_ref,
                    )
                    .where(
                        ReplyDispatchControl.workspace_ref == runtime.workspace,
                        ReplyConnectorAdmission.connector_key == "telegram_business",
                    )
                )
            ).one_or_none()
            if (
                final is None
                or final[0] != control.mode
                or final[1] != control.generation
                or not final[2]
                or final[3] != "healthy"
                or final[4] != epoch.id
                or final[5] is None
                or final[5] <= final[6]
                or runtime.owner != member["boot_id"]
                or config_digest(runtime) != loaded_config
            ):
                raise ContinuityDenied("continuity_process_admission_changed")
    except ContinuityDenied:
        raise
    except Exception:
        # SQLAlchemy/provider-specific exception text can contain connection
        # details. Only this bounded local denial crosses the transport bridge.
        raise ContinuityDenied("continuity_process_health_unavailable") from None
