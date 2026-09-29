"""Actual PostgreSQL admission for the loaded primary-bot process, with zero HTTP."""

from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from app.infrastructure.reply_sequence_epoch import ContinuityDenied
from app.infrastructure.reply_sequence_process_health import require_verified_process_health
from app.models.domain import TelegramBusinessConnection, TelegramReceipt
from app.models.reply_sequences import (
    ReplyConnectorAdmission,
    ReplyContinuityRenewal,
    ReplyDispatchControl,
    ReplyIngressProcess,
)
from sqlalchemy import func, select, text

from test_reply_sends_postgres import _seed_reply
from test_reply_sequence_dispatch_postgres import runtime_fixture
from test_reply_sequence_epoch_postgres import prepare_epoch


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def ledger(sessions, revisions=("0115_reply_canary_binding",)):
    async with sessions() as session:
        await session.execute(text("CREATE TABLE alembic_version (version_num varchar(64))"))
        for revision in revisions:
            await session.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
            )
        await session.commit()


async def fingerprint(runtime, sessions):
    async with sessions() as session:
        control = await session.get(ReplyDispatchControl, runtime.workspace)
        connector = (await session.scalars(select(ReplyConnectorAdmission))).one()
        return (
            control.mode,
            control.generation,
            connector.version,
            connector.epoch_id,
            connector.observed_at,
            connector.continuity_until,
            await session.scalar(select(func.count()).select_from(ReplyContinuityRenewal)),
        )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["serial", "parallel"])
async def test_current_verified_primary_process_passes_without_renewal_or_mutation(mode):
    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        await prepare_epoch(runtime, sessions)
        await ledger(sessions)
        async with sessions() as session:
            (await session.get(ReplyDispatchControl, runtime.workspace)).mode = mode
            await session.commit()
        before = await fingerprint(runtime, sessions)
        await require_verified_process_health(runtime, bot_resource_id=runtime.bot_resource)
        assert await fingerprint(runtime, sessions) == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    "revisions",
    [(), ("0111_reply_documents",), ("0115_reply_canary_binding", "0114_reply_continuity_epoch")],
)
async def test_real_ledger_must_have_exact_single_0115(revisions):
    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        await prepare_epoch(runtime, sessions)
        await ledger(sessions, revisions)
        with pytest.raises(ContinuityDenied, match="continuity_process_schema_mismatch"):
            await require_verified_process_health(runtime, bot_resource_id=runtime.bot_resource)


@pytest.mark.anyio
async def test_missing_real_ledger_is_local_redacted_denial():
    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        await prepare_epoch(runtime, sessions)
        with pytest.raises(ContinuityDenied, match="^continuity_process_health_unavailable$"):
            await require_verified_process_health(runtime, bot_resource_id=runtime.bot_resource)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        "disabled",
        "migration_incomplete",
        "epoch_missing",
        "connector_blocked",
        "expired",
        "restart",
        "source_drift",
        "config_drift",
        "global_send_config_drift",
        "observed_source_drift",
        "stale_process",
        "gap",
        "rebind",
        "extra_process",
        "backlog",
        "missing_projection",
        "dead_letter",
        "false_flags",
        "unknown_source",
        "primary_token_drift",
    ],
)
async def test_each_unverified_or_unhealthy_process_denies_before_http(failure):
    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        start, _ = await prepare_epoch(runtime, sessions)
        await ledger(sessions)
        async with sessions() as session:
            control = await session.get(ReplyDispatchControl, runtime.workspace)
            connector = (await session.scalars(select(ReplyConnectorAdmission))).one()
            process = await session.get(ReplyIngressProcess, (runtime.workspace, runtime.owner))
            if failure == "disabled":
                control.mode = "disabled"
            elif failure == "migration_incomplete":
                control.migration_complete = False
            elif failure == "epoch_missing":
                connector.epoch_id = None
            elif failure == "connector_blocked":
                connector.state = "blocked"
            elif failure == "expired":
                connector.continuity_until = start
            elif failure == "restart":
                runtime.owner = uuid4().hex
            elif failure == "source_drift":
                runtime.settings.deployment_sha = "a" * 40
            elif failure == "config_drift":
                runtime.settings.gate_reply_sequence_canary_enabled = True
            elif failure == "global_send_config_drift":
                runtime.settings.gate_send_enabled = False
            elif failure == "observed_source_drift":
                process.source_sha = "a" * 40
            elif failure == "stale_process":
                process.observed_at = start - timedelta(seconds=121)
            elif failure == "gap":
                process.last_gap_at = start
            elif failure == "rebind":
                connection = (await session.scalars(select(TelegramBusinessConnection))).one()
                connection.rights = {"can_reply": False}
            elif failure == "extra_process":
                session.add(
                    ReplyIngressProcess(
                        workspace_ref=runtime.workspace,
                        boot_id=uuid4().hex,
                        source_sha=process.source_sha,
                        config_sha256=process.config_sha256,
                        bot_resource_id=process.bot_resource_id,
                        observed_at=process.observed_at,
                        workers=process.workers,
                    )
                )
            elif failure in {"backlog", "missing_projection", "dead_letter"}:
                session.add(
                    TelegramReceipt(
                        bot_key="telegram_business",
                        update_id=773311,
                        payload={},
                        received_at=start,
                        created_at=(
                            start - timedelta(seconds=121) if failure == "backlog" else start
                        ),
                        status="dead_letter" if failure == "dead_letter" else "received",
                    )
                )
            elif failure == "false_flags":
                runtime.settings.gate_reply_sequence_stop_on_inbound_enabled = False
                runtime.settings.gate_reply_sequence_dispatch_v2_enabled = False
            elif failure == "unknown_source":
                runtime.settings.deployment_sha = "unknown"
            elif failure == "primary_token_drift":
                runtime.settings.telegram_bot_token = "456:synthetic-other"
            await session.commit()
        before = await fingerprint(runtime, sessions)
        with pytest.raises(ContinuityDenied):
            await require_verified_process_health(runtime, bot_resource_id=runtime.bot_resource)
        assert await fingerprint(runtime, sessions) == before


@pytest.mark.anyio
async def test_other_bot_argument_cannot_borrow_verified_primary_epoch():
    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        await prepare_epoch(runtime, sessions)
        await ledger(sessions)
        with pytest.raises(ContinuityDenied, match="^continuity_primary_bot_mismatch$"):
            await require_verified_process_health(runtime, bot_resource_id="f" * 64)


@pytest.mark.anyio
async def test_database_error_is_safe_denial_without_original_exception_text(monkeypatch):
    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        await prepare_epoch(runtime, sessions)

        @asynccontextmanager
        async def failed_sessions():
            raise RuntimeError("synthetic_connection_detail_must_not_escape")
            yield  # pragma: no cover

        monkeypatch.setattr(runtime, "sessions", failed_sessions)
        with pytest.raises(ContinuityDenied) as error:
            await require_verified_process_health(runtime, bot_resource_id=runtime.bot_resource)
        assert str(error.value) == "continuity_process_health_unavailable"
        assert error.value.__suppress_context__ is True


@pytest.mark.anyio
async def test_disable_from_another_transaction_during_health_scan_denies(monkeypatch):
    from app.infrastructure import reply_sequence_process_health as process_health

    async with runtime_fixture() as (runtime, sessions):
        await _seed_reply(sessions, draft_count=2)
        await prepare_epoch(runtime, sessions)
        await ledger(sessions)
        original_validate = process_health.validate_durable_health
        before = await fingerprint(runtime, sessions)

        async def validate_then_disable(session, epoch, now):
            result = await original_validate(session, epoch, now)
            # This distinct transaction represents an emergency stop arriving
            # while the read-only primary callback is inspecting fleet evidence.
            async with sessions() as writer:
                control = await writer.get(ReplyDispatchControl, runtime.workspace)
                control.mode = "disabled"
                control.generation += 1
                await writer.commit()
            return result

        monkeypatch.setattr(process_health, "validate_durable_health", validate_then_disable)
        with pytest.raises(ContinuityDenied, match="^continuity_process_admission_changed$"):
            await require_verified_process_health(runtime, bot_resource_id=runtime.bot_resource)
        after = await fingerprint(runtime, sessions)
        assert after[0] == "disabled" and after[1] == before[1] + 1
        assert after[2:] == before[2:]
