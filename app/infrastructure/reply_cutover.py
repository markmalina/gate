"""Read-only, baseline-compatible observations for the #745 cutover."""

from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import SendAttempt, SendAttemptStatus

BASELINE_REVISION = "0115_watch_voice_post_payment"
GUARDED_REVISION = "0116_reply_dialog_versions"


@dataclass(frozen=True, slots=True)
class ReplyCutoverObservation:
    """Counts describe stored attempts, never provider outcomes or fleet state."""

    schema_revisions: tuple[str, ...]
    reserved: int
    sending: int
    unknown: int

    @property
    def active_attempts(self) -> int:
        return self.reserved + self.sending

    def require_migration_preconditions(self) -> None:
        if self.schema_revisions not in ((BASELINE_REVISION,), (GUARDED_REVISION,)):
            raise RuntimeError("reply_cutover_schema_unexpected")
        if self.active_attempts:
            raise RuntimeError("reply_cutover_attempts_not_drained")


async def observe_reply_cutover(session: AsyncSession) -> ReplyCutoverObservation:
    """Use only tables/columns present before 0116; do not read message payloads."""

    revisions = await session.execute(text("SELECT version_num FROM alembic_version"))
    counts = await session.execute(
        select(SendAttempt.status, func.count())
        .where(SendAttempt.status.in_((
            SendAttemptStatus.RESERVED.value,
            SendAttemptStatus.SENDING.value,
            SendAttemptStatus.UNKNOWN.value,
        )))
        .group_by(SendAttempt.status)
    )
    statuses = {str(attempt_status): int(count) for attempt_status, count in counts.all()}
    return ReplyCutoverObservation(
        schema_revisions=tuple(sorted(str(revision) for revision in revisions.scalars().all())),
        reserved=statuses.get(SendAttemptStatus.RESERVED.value, 0),
        sending=statuses.get(SendAttemptStatus.SENDING.value, 0),
        unknown=statuses.get(SendAttemptStatus.UNKNOWN.value, 0),
    )
