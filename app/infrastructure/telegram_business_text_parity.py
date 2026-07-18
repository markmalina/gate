"""Read-only legacy parity adapter for canonical Telegram Business text."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.telegram_business_text_parity import (
    TelegramBusinessLegacyTextFact,
    TelegramBusinessTextParityCommand,
    TelegramBusinessTextParityError,
    TelegramBusinessTextParityObserver,
    TelegramBusinessTextParityResult,
    TelegramBusinessTextParityState,
    TelegramBusinessTextParityStatus,
    classify_telegram_business_text_parity_error,
    compare_telegram_business_text_parity,
)
from app.models.canonical import (
    CanonicalMessage,
    TelegramBusinessTextParityEvidence,
    Workspace,
)
from app.models.domain import (
    Account,
    Message,
    ReplyTask,
    TelegramBusinessConnection,
)

TELEGRAM_BUSINESS_TEXT_PARITY_DIRECTION_POLICY_VERSION = 2


class SessionFactoryTelegramBusinessTextParityObserver(
    TelegramBusinessTextParityObserver
):
    """Compare one fact and commit only content-free evidence in a short transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def observe(
        self,
        command: TelegramBusinessTextParityCommand,
    ) -> TelegramBusinessTextParityResult:
        parity_error: TelegramBusinessTextParityError | None = None
        result: TelegramBusinessTextParityResult | None = None
        async with self._sessions.begin() as session:
            rows = (
                await session.execute(
                    select(
                        Account.alias,
                        Message.direction,
                        Message.chat_id,
                        Message.business_connection_id,
                        Message.text,
                        func.count(ReplyTask.id),
                    )
                    .join(Account, Account.id == Message.account_id)
                    .outerjoin(
                        ReplyTask,
                        ReplyTask.trigger_message_id == Message.id,
                    )
                    .where(
                        Message.telegram_update_id == command.update_id,
                        Message.source == "telegram",
                        Message.business_connection_id.is_not(None),
                    )
                    .group_by(
                        Account.alias,
                        Message.id,
                        Message.direction,
                        Message.chat_id,
                        Message.business_connection_id,
                        Message.text,
                    )
                )
            ).all()
            try:
                result = _compare_rows(command, rows)
            except TelegramBusinessTextParityError as exc:
                parity_error = exc
                await _record_evidence(
                    session,
                    command=command,
                    state=classify_telegram_business_text_parity_error(exc.code),
                    mismatch_code=exc.code,
                )
            else:
                await _record_evidence(
                    session,
                    command=command,
                    state=TelegramBusinessTextParityState.MATCHED,
                    mismatch_code=None,
                )
        if parity_error is not None:
            raise parity_error
        if result is None:
            raise RuntimeError("Telegram Business text parity result missing")
        return result


def _compare_rows(
    command: TelegramBusinessTextParityCommand,
    rows: Sequence[
        Row[tuple[str, str, str, str | None, str | None, int]]
    ],
) -> TelegramBusinessTextParityResult:
    if not rows:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_legacy_pending"
        )
    if len(rows) != 1:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_legacy_cardinality"
        )
    (
        account_alias,
        direction,
        provider_dialog_id,
        business_connection_id,
        text_content,
        reply_task_count,
    ) = rows[0]
    if (
        not isinstance(account_alias, str)
        or not isinstance(direction, str)
        or not isinstance(provider_dialog_id, str)
        or not isinstance(business_connection_id, str)
        or not isinstance(text_content, str)
        or not isinstance(reply_task_count, int)
    ):
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_legacy_invalid"
        )
    return compare_telegram_business_text_parity(
        command,
        TelegramBusinessLegacyTextFact(
            account_alias=account_alias,
            direction=direction,
            provider_dialog_id=provider_dialog_id,
            business_connection_id=business_connection_id,
            text_content=text_content,
            reply_task_count=reply_task_count,
        ),
    )


async def _record_evidence(
    session: AsyncSession,
    *,
    command: TelegramBusinessTextParityCommand,
    state: TelegramBusinessTextParityState,
    mismatch_code: str | None,
) -> None:
    """Upsert one scoped row without copying message or provider identity."""

    try:
        account_id = uuid.UUID(command.account_id)
        canonical_message_id = uuid.UUID(command.canonical_message_id)
    except ValueError as exc:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_scope_invalid"
        ) from exc
    workspace_id = await session.scalar(
        select(Workspace.id)
        .join(
            CanonicalMessage,
            CanonicalMessage.workspace_id == Workspace.id,
        )
        .where(
            Workspace.key == command.workspace_key,
            Workspace.is_active.is_(True),
            CanonicalMessage.id == canonical_message_id,
        )
    )
    if workspace_id is None:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_scope_invalid"
        )
    account_exists = await session.scalar(
        select(Account.id)
        .join(
            TelegramBusinessConnection,
            TelegramBusinessConnection.account_id == Account.id,
        )
        .where(
            Account.id == account_id,
            Account.alias == command.account_alias,
            Account.is_active.is_(True),
            TelegramBusinessConnection.business_connection_id
            == command.business_connection_id,
            TelegramBusinessConnection.is_enabled.is_(True),
        )
    )
    if account_exists is None:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_scope_invalid"
        )
    values = {
        "workspace_id": workspace_id,
        "account_id": account_id,
        "canonical_message_id": canonical_message_id,
        "state": state.value,
        "mismatch_code": mismatch_code,
        "direction_policy_version": (
            TELEGRAM_BUSINESS_TEXT_PARITY_DIRECTION_POLICY_VERSION
        ),
    }
    statement = insert(TelegramBusinessTextParityEvidence).values(**values)
    evidence_id = await session.scalar(
        statement.on_conflict_do_update(
            index_elements=[TelegramBusinessTextParityEvidence.canonical_message_id],
            set_={
                "state": statement.excluded.state,
                "mismatch_code": statement.excluded.mismatch_code,
                "direction_policy_version": (
                    statement.excluded.direction_policy_version
                ),
                "observation_count": (
                    TelegramBusinessTextParityEvidence.observation_count + 1
                ),
                "last_observed_at": func.now(),
            },
            where=(
                TelegramBusinessTextParityEvidence.workspace_id
                == statement.excluded.workspace_id
            )
            & (
                TelegramBusinessTextParityEvidence.account_id
                == statement.excluded.account_id
            ),
        ).returning(TelegramBusinessTextParityEvidence.id)
    )
    if evidence_id is None:
        raise TelegramBusinessTextParityError(
            "telegram_business_text_parity_scope_invalid"
        )


async def telegram_business_text_parity_status(
    session: AsyncSession,
    *,
    workspace_key: str,
    account_ids: frozenset[uuid.UUID],
) -> TelegramBusinessTextParityStatus:
    """Return account-scoped, content-free cutover evidence aggregates."""

    if not account_ids:
        return _status(
            matched=0,
            pending=0,
            mismatch=0,
            required_accounts=0,
            covered_accounts=0,
            mismatch_codes=(),
        )
    workspace_id = await session.scalar(
        select(Workspace.id).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    if workspace_id is None:
        return _status(
            matched=0,
            pending=0,
            mismatch=0,
            required_accounts=0,
            covered_accounts=0,
            mismatch_codes=(),
        )
    count_rows = (
        await session.execute(
            select(
                TelegramBusinessTextParityEvidence.state,
                func.count(TelegramBusinessTextParityEvidence.id),
            )
            .where(
                TelegramBusinessTextParityEvidence.workspace_id == workspace_id,
                TelegramBusinessTextParityEvidence.account_id.in_(account_ids),
                TelegramBusinessTextParityEvidence.direction_policy_version
                == TELEGRAM_BUSINESS_TEXT_PARITY_DIRECTION_POLICY_VERSION,
            )
            .group_by(TelegramBusinessTextParityEvidence.state)
        )
    ).all()
    counts = {str(state): int(count) for state, count in count_rows}
    mismatch_rows = (
        await session.execute(
            select(
                TelegramBusinessTextParityEvidence.mismatch_code,
                func.count(TelegramBusinessTextParityEvidence.id),
            )
            .where(
                TelegramBusinessTextParityEvidence.workspace_id == workspace_id,
                TelegramBusinessTextParityEvidence.account_id.in_(account_ids),
                TelegramBusinessTextParityEvidence.direction_policy_version
                == TELEGRAM_BUSINESS_TEXT_PARITY_DIRECTION_POLICY_VERSION,
                TelegramBusinessTextParityEvidence.state
                == TelegramBusinessTextParityState.MISMATCH.value,
            )
            .group_by(TelegramBusinessTextParityEvidence.mismatch_code)
            .order_by(TelegramBusinessTextParityEvidence.mismatch_code)
        )
    ).all()
    covered_accounts = int(
        (
            await session.scalar(
                select(
                    func.count(
                        distinct(TelegramBusinessTextParityEvidence.account_id)
                    )
                ).where(
                    TelegramBusinessTextParityEvidence.workspace_id == workspace_id,
                    TelegramBusinessTextParityEvidence.account_id.in_(account_ids),
                    TelegramBusinessTextParityEvidence.direction_policy_version
                    == TELEGRAM_BUSINESS_TEXT_PARITY_DIRECTION_POLICY_VERSION,
                    TelegramBusinessTextParityEvidence.state
                    == TelegramBusinessTextParityState.MATCHED.value,
                )
            )
        )
        or 0
    )
    required_accounts = int(
        (
            await session.scalar(
                select(func.count(distinct(TelegramBusinessConnection.account_id)))
                .join(Account, Account.id == TelegramBusinessConnection.account_id)
                .where(
                    TelegramBusinessConnection.account_id.in_(account_ids),
                    TelegramBusinessConnection.is_enabled.is_(True),
                    Account.is_active.is_(True),
                )
            )
        )
        or 0
    )
    return _status(
        matched=counts.get(TelegramBusinessTextParityState.MATCHED.value, 0),
        pending=counts.get(TelegramBusinessTextParityState.PENDING.value, 0),
        mismatch=counts.get(TelegramBusinessTextParityState.MISMATCH.value, 0),
        required_accounts=required_accounts,
        covered_accounts=covered_accounts,
        mismatch_codes=tuple(
            (str(code), int(count))
            for code, count in mismatch_rows
            if code is not None
        ),
    )


def _status(
    *,
    matched: int,
    pending: int,
    mismatch: int,
    required_accounts: int,
    covered_accounts: int,
    mismatch_codes: tuple[tuple[str, int], ...],
) -> TelegramBusinessTextParityStatus:
    total = matched + pending + mismatch
    cutover_ready = (
        required_accounts > 0
        and covered_accounts == required_accounts
        and matched > 0
        and pending == 0
        and mismatch == 0
    )
    if mismatch:
        state = "blocked"
    elif cutover_ready:
        state = "ready"
    elif total:
        state = "collecting"
    else:
        state = "no_evidence"
    return TelegramBusinessTextParityStatus(
        state=state,
        total_count=total,
        matched_count=matched,
        pending_count=pending,
        mismatch_count=mismatch,
        required_account_count=required_accounts,
        covered_account_count=covered_accounts,
        mismatch_codes=mismatch_codes,
        cutover_ready=cutover_ready,
    )
