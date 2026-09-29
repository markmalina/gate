"""Bounded durable #716 worker; no per-series sleeping and no batch barrier."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from functools import partial

from app.application.reply_sends import (
    ReplySendError,
    SendApprovedReplyCommand,
    SendApprovedReplyService,
)
from app.application.worker_health import REPLY_SEQUENCE_WORKER, WorkerHealthReporter
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.infrastructure.reply_sequence_guard import SequenceGuardUnavailable, dialog_guard
from app.infrastructure.reply_sequence_runtime import DispatchClaim, ReplySequenceRuntime
from app.services.telegram import TelegramClient
from app.worker.reply_sequence_supervisor import DispatchCompletion, ReplySequenceSupervisor

logger = logging.getLogger(__name__)


async def dispatch_claim(
    runtime: ReplySequenceRuntime, telegram: TelegramClient, claim: DispatchClaim
) -> None:
    try:
        async with runtime.sessions() as session:
            principal = Principal(
                subject=claim.principal_subject,
                auth_method="approved-reply-sequence",
                scopes=frozenset({"gate:messages:send"}),
                access_scope=AccessScope(
                    workspace_id=claim.workspace,
                    account_ids=frozenset({claim.account_id}),
                    project_ids=frozenset({claim.project_id}),
                ),
            )
            async with asyncio.timeout(30):
                await SendApprovedReplyService(
                    SqlTelegramReplySender(
                        session=session,
                        telegram=telegram,
                        settings=runtime.settings,
                        dispatch_claim=claim,
                    )
                ).execute(
                    SendApprovedReplyCommand(
                        principal=principal,
                        draft_id=claim.draft_id,
                        confirmed=True,
                        expected_account=claim.account_label,
                        expected_recipient_chat_id=claim.recipient,
                    )
                )
    except (ReplySendError, SequenceGuardUnavailable) as exc:
        code = exc.code if isinstance(exc, ReplySendError) else "sequence_guard_unavailable"
        retryable = {
            "sequence_rate_wait",
            "sequence_not_due",
            "sequence_guard_unavailable",
            "sequence_config_mismatch",
            "sequence_claim_stale",
        }
        async with dialog_guard(
            runtime.ingress_guards,
            workspace=claim.workspace,
            account_id=claim.account_id,
            chat_id=claim.recipient,
        ):
            await runtime.release_preflight(claim, error=None if code in retryable else code)
    finally:
        # If no ledger admission exists (including cancelled preflight), release only
        # the matching token. Admitted/ambiguous attempts are left to reconciliation.
        await runtime.release_preflight(claim)


async def run_dispatch_loop(
    runtime: ReplySequenceRuntime,
    telegram: TelegramClient,
    *,
    health_reporter: WorkerHealthReporter | None = None,
) -> None:
    pool = ReplySequenceSupervisor(max_inflight=runtime.settings.gate_reply_sequence_max_inflight)
    pending: tuple[DispatchCompletion, ...] = ()
    try:
        while True:
            try:
                completions = pending + pool.collect_finished()
                pending = ()
                await runtime.reconcile(limit=100)
                scanned = 0
                while pool.available_capacity and scanned < 1000:
                    candidates = await runtime.candidates(limit=100)
                    if not candidates:
                        break
                    scanned += len(candidates)
                    for sequence_id in candidates:
                        if not pool.available_capacity:
                            break
                        claim = await runtime.claim(sequence_id)
                        if claim is not None:
                            if not pool.try_start(
                                claim.sequence_id, partial(dispatch_claim, runtime, telegram, claim)
                            ):
                                await runtime.release_preflight(claim)
                    if runtime.cursor is None:
                        break
                reason = (
                    await runtime.health_reason(closed_dispatch_ok=True)
                    if health_reporter is not None
                    else None
                )
                if health_reporter is not None and (
                    reason or any(c.outcome == "failed" for c in completions)
                ):
                    health_reporter.record_failure(
                        REPLY_SEQUENCE_WORKER,
                        error_code=reason or "reply_sequence_dispatch_failed",
                        observed_at=datetime.now(UTC),
                    )
                elif health_reporter is not None:
                    health_reporter.record_success(
                        REPLY_SEQUENCE_WORKER, observed_at=datetime.now(UTC)
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                if health_reporter is not None:
                    health_reporter.record_failure(
                        REPLY_SEQUENCE_WORKER,
                        error_code="reply_sequence_iteration_failed",
                        observed_at=datetime.now(UTC),
                    )
                logger.error("Reply dispatch iteration failed; durable ownership retained")
            if pool.available_capacity == runtime.settings.gate_reply_sequence_max_inflight:
                await asyncio.sleep(runtime.settings.gate_reply_sequence_poll_seconds)
            else:
                pending = await pool.wait_for_completion(
                    timeout=runtime.settings.gate_reply_sequence_poll_seconds
                )
    finally:
        report = await pool.shutdown()
        if report.unresolved:
            # No permit is freed by local task cancellation. Recovery quarantines
            # durable sending attempts; the process must not reactivate this pool.
            runtime.shutdown_supervisor = pool
            try:
                async with asyncio.timeout(2):
                    await runtime.quarantine_shutdown(report.unresolved)
            finally:
                logger.critical(
                    "Reply dispatch shutdown unresolved_count=%s; terminating",
                    len(report.unresolved),
                )
                # Releasing a session barrier while a non-cooperative HTTP task
                # survives would break the stop contract. Terminate this process;
                # PostgreSQL recovery keeps admitted attempts ambiguous, never retries.
                os._exit(70)
