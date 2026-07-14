"""PostgreSQL recovery for stale reply-send attempts."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.reply_send_recovery import (
    RecoverStaleReplySendsCommand,
    ReplySendRecovery,
    ReplySendRecoveryResult,
)
from app.models.domain import AuditLog, SendAttempt, SendAttemptStatus

_RECOVERY_ACTOR = "gate-reply-send-recovery"
_RECOVERY_ACTION = "reply_send_stale_attempts_recovered"
_SENDING_RECOVERY_CODE = "delivery_outcome_unknown_after_recovery"
_RESERVED_RECOVERY_CODE = "reservation_expired_before_delivery"


class SqlReplySendRecovery(ReplySendRecovery):
    """Close a bounded stale-attempt batch without touching reply context."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def recover_stale(
        self,
        command: RecoverStaleReplySendsCommand,
    ) -> ReplySendRecoveryResult:
        try:
            result = await self._session.execute(
                select(SendAttempt)
                .where(
                    or_(
                        and_(
                            SendAttempt.status == SendAttemptStatus.SENDING.value,
                            func.coalesce(
                                SendAttempt.started_at,
                                SendAttempt.created_at,
                            )
                            <= command.stale_before,
                        ),
                        and_(
                            SendAttempt.status == SendAttemptStatus.RESERVED.value,
                            SendAttempt.reserved_at <= command.stale_before,
                        ),
                    )
                )
                .order_by(SendAttempt.created_at, SendAttempt.id)
                .limit(command.limit)
                .with_for_update(skip_locked=True)
            )
            attempts = result.scalars().all()
            sending_marked_unknown = 0
            reserved_cancelled = 0

            for attempt in attempts:
                if attempt.status == SendAttemptStatus.SENDING.value:
                    attempt.status = SendAttemptStatus.UNKNOWN.value
                    attempt.error_code = _SENDING_RECOVERY_CODE
                    sending_marked_unknown += 1
                else:
                    attempt.status = SendAttemptStatus.CANCELLED.value
                    attempt.error_code = _RESERVED_RECOVERY_CODE
                    reserved_cancelled += 1
                attempt.error_detail = None
                attempt.finished_at = command.recovered_at

            recovery = ReplySendRecoveryResult(
                recovered=len(attempts),
                sending_marked_unknown=sending_marked_unknown,
                reserved_cancelled=reserved_cancelled,
            )
            if recovery.recovered:
                self._session.add(
                    AuditLog(
                        actor=_RECOVERY_ACTOR,
                        action=_RECOVERY_ACTION,
                        metadata_={
                            "recovered": recovery.recovered,
                            "sending_marked_unknown": (recovery.sending_marked_unknown),
                            "reserved_cancelled": recovery.reserved_cancelled,
                            "stale_after_seconds": int(
                                (command.recovered_at - command.stale_before).total_seconds()
                            ),
                            "limit": command.limit,
                        },
                    )
                )

            await self._session.commit()
            return recovery
        except Exception:
            await self._session.rollback()
            raise
