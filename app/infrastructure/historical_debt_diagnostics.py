"""Owner-scoped, payload-private diagnostics for historical Gate debt."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    BillingBudgetReservationModel,
    BillingLedgerEntryModel,
    BillingUsageEventModel,
)
from app.models.canonical import (
    CanonicalMessage,
    Dialog,
    TelegramBusinessTextParityEvidence,
    Workspace,
)
from app.models.domain import (
    Account,
    Message,
    PersonIdentity,
    ReplyTask,
    TelegramOutboxEvent,
    TelegramReceipt,
    TelegramVoiceArchive,
)

JsonObject = dict[str, Any]
_VOICE_CODES = (
    "billing_reservation_replayed",
    "openai_transcription_transport_unknown",
)
_PARITY_CODE = "telegram_business_text_parity_reply_task_mismatch"


async def historical_debt_diagnostics(
    session: AsyncSession,
    *,
    workspace_key: str,
    account_ids: frozenset[UUID],
    project_ids: frozenset[UUID],
) -> JsonObject:
    """Return only redacted facts for the six bounded historical records."""

    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.key == workspace_key,
            Workspace.is_active.is_(True),
        )
    )
    if workspace is None or not account_ids or not project_ids:
        return _empty_payload()
    voice = await _voice_records(
        session,
        workspace=workspace,
        account_ids=account_ids,
        project_ids=project_ids,
    )
    parity = await _parity_records(
        session,
        workspace=workspace,
        account_ids=account_ids,
        project_ids=project_ids,
    )
    return {
        "voice_count": len(voice),
        "parity_count": len(parity),
        "voice_records": voice,
        "parity_records": parity,
        "private_content_exposed": False,
        "provider_identifiers_exposed": False,
        "billing_identifiers_exposed": False,
        "mutation_allowed": False,
        "provider_call_allowed": False,
    }


async def _voice_records(
    session: AsyncSession,
    *,
    workspace: Workspace,
    account_ids: frozenset[UUID],
    project_ids: frozenset[UUID],
) -> list[JsonObject]:
    rows = (
        await session.execute(
            select(
                TelegramReceipt,
                TelegramOutboxEvent,
                TelegramVoiceArchive,
                Account.country_code,
            )
            .join(
                TelegramOutboxEvent,
                TelegramOutboxEvent.receipt_id == TelegramReceipt.id,
            )
            .join(
                TelegramVoiceArchive,
                TelegramVoiceArchive.receipt_id == TelegramReceipt.id,
            )
            .join(Account, Account.id == TelegramVoiceArchive.account_id)
            .where(
                TelegramReceipt.bot_key == "telegram_business",
                TelegramReceipt.status == "dead_letter",
                TelegramReceipt.last_error_code.in_(_VOICE_CODES),
                TelegramOutboxEvent.status == "dead_letter",
                TelegramOutboxEvent.last_error_code
                == TelegramReceipt.last_error_code,
                TelegramVoiceArchive.account_id.in_(account_ids),
                TelegramVoiceArchive.project_id.in_(project_ids),
            )
            .order_by(TelegramReceipt.received_at, TelegramReceipt.id)
        )
    ).all()
    result: list[JsonObject] = []
    for receipt, event, archive, country_code in rows:
        message_key, message = _message_payload(receipt.payload)
        original_key = (
            f"telegram:{message_key}:{receipt.update_id}:"
            f"{archive.telegram_message_id}"
        )
        recovery_key = f"telegram-voice-recovery-v2:{archive.id}"
        reservations = list(
            (
                await session.scalars(
                    select(BillingBudgetReservationModel)
                    .where(
                        BillingBudgetReservationModel.workspace_id
                        == workspace.id,
                        BillingBudgetReservationModel.reservation_key.in_(
                            (original_key, recovery_key)
                        ),
                    )
                    .order_by(BillingBudgetReservationModel.created_at)
                )
            ).all()
        )
        operation_digests = [
            sha256(row.reservation_key.encode()).hexdigest()
            for row in reservations
        ]
        event_keys = [f"billing-usage:{value}" for value in operation_digests]
        usage_count = await _count_usage(session, workspace.id, event_keys)
        ledger_count = await _count_ledger(session, workspace.id, event_keys)
        canonical_count = int(
            (
                await session.scalar(
                    select(func.count(CanonicalMessage.id)).where(
                        CanonicalMessage.workspace_id == workspace.id,
                        CanonicalMessage.receipt_id == receipt.id,
                    )
                )
            )
            or 0
        )
        legacy_count = int(
            (
                await session.scalar(
                    select(func.count(Message.id)).where(
                        Message.telegram_update_id == receipt.update_id,
                        Message.account_id == archive.account_id,
                        Message.project_id == archive.project_id,
                        Message.chat_id == archive.chat_id,
                        Message.business_connection_id
                        == archive.business_connection_id,
                    )
                )
            )
            or 0
        )
        actor_id = _counterparty_id(message, archive.direction)
        identities = await _verified_identities(
            session,
            account_id=archive.account_id,
            platform_user_id=actor_id,
        )
        terminal_states = {"settled", "released", "unknown"}
        provider_attempts = sum(
            int(row.state in terminal_states) for row in reservations
        )
        original = next(
            (row for row in reservations if row.reservation_key == original_key),
            None,
        )
        transcript_complete = bool(
            archive.transcription_status == "transcribed"
            and archive.transcript_text
        )
        reason_code = str(receipt.last_error_code)
        result.append(
            {
                "receipt_id": str(receipt.id),
                "event_id": str(event.id),
                "archive_id": str(archive.id),
                "record_ref": _opaque_ref(receipt.id),
                "reason_code": reason_code,
                "received_at": _iso(receipt.received_at),
                "last_error_at": _iso(receipt.last_error_at),
                "workspace_id": str(workspace.id),
                "account_id": str(archive.account_id),
                "project_id": str(archive.project_id),
                "country_code": country_code,
                "chat_scope_matched": _chat_matches(message, archive.chat_id),
                "business_connection_scope_matched": (
                    _business_connection(message)
                    == archive.business_connection_id
                ),
                "verified_identity_count": len(identities),
                "telegram_identity_confirmed": len(identities) == 1,
                "logical_operation_sha256": sha256(
                    original_key.encode()
                ).hexdigest(),
                "reservation_count": len(reservations),
                "reservation_states": [row.state for row in reservations],
                "original_reservation_state": (
                    original.state if original is not None else None
                ),
                "settlement_count": sum(
                    int(row.state == "settled") for row in reservations
                ),
                "release_count": sum(
                    int(row.state == "released") for row in reservations
                ),
                "unknown_count": sum(
                    int(row.state == "unknown") for row in reservations
                ),
                "usage_event_count": usage_count,
                "ledger_entry_count": ledger_count,
                "provider_attempt_count": provider_attempts,
                "archive_status": archive.transcription_status,
                "transcript_complete": transcript_complete,
                "summary_complete": bool(
                    archive.summary_status == "summarized"
                    and archive.summary_text
                ),
                "provider_result_saved": transcript_complete or usage_count > 0,
                "canonical_projection_count": canonical_count,
                "legacy_projection_count": legacy_count,
                "billing_verdict": _billing_verdict(
                    reason_code=reason_code,
                    original_reservation_exists=original is not None,
                    reservation_count=len(reservations),
                    usage_count=usage_count,
                    ledger_count=ledger_count,
                ),
                "transport_verdict": _transport_verdict(
                    reason_code=reason_code,
                    archive_present=True,
                    transcript_complete=transcript_complete,
                ),
                "projection_recovery_allowed": transcript_complete,
            }
        )
    return result


async def _parity_records(
    session: AsyncSession,
    *,
    workspace: Workspace,
    account_ids: frozenset[UUID],
    project_ids: frozenset[UUID],
) -> list[JsonObject]:
    rows = (
        await session.execute(
            select(
                TelegramBusinessTextParityEvidence,
                CanonicalMessage,
                TelegramReceipt,
                Dialog,
            )
            .join(
                CanonicalMessage,
                CanonicalMessage.id
                == TelegramBusinessTextParityEvidence.canonical_message_id,
            )
            .join(
                TelegramReceipt,
                TelegramReceipt.id == CanonicalMessage.receipt_id,
            )
            .join(Dialog, Dialog.id == CanonicalMessage.dialog_id)
            .where(
                TelegramBusinessTextParityEvidence.workspace_id
                == workspace.id,
                TelegramBusinessTextParityEvidence.account_id.in_(account_ids),
                TelegramBusinessTextParityEvidence.state == "mismatch",
                TelegramBusinessTextParityEvidence.mismatch_code
                == _PARITY_CODE,
            )
            .order_by(
                TelegramBusinessTextParityEvidence.first_observed_at,
                TelegramBusinessTextParityEvidence.id,
            )
        )
    ).all()
    result: list[JsonObject] = []
    for evidence, canonical, receipt, dialog in rows:
        messages = list(
            (
                await session.scalars(
                    select(Message).where(
                        Message.telegram_update_id == receipt.update_id,
                        Message.source == "telegram",
                        Message.business_connection_id.is_not(None),
                    )
                )
            ).all()
        )
        tasks: list[ReplyTask] = []
        if len(messages) == 1:
            tasks = list(
                (
                    await session.scalars(
                        select(ReplyTask).where(
                            ReplyTask.trigger_message_id == messages[0].id
                        )
                    )
                ).all()
            )
        message_key, raw_message = _message_payload(receipt.payload)
        del message_key
        message = messages[0] if len(messages) == 1 else None
        actor_id = _counterparty_id(raw_message, canonical.direction)
        identities = await _verified_identities(
            session,
            account_id=evidence.account_id,
            platform_user_id=actor_id,
        )
        account_matched = bool(
            message is not None and message.account_id == evidence.account_id
        )
        project_matched = bool(
            message is not None and message.project_id in project_ids
        )
        chat_matched = bool(
            message is not None and message.chat_id == dialog.provider_dialog_id
        )
        business_matched = bool(
            message is not None
            and message.business_connection_id
            == _business_connection(raw_message)
        )
        text_matched = bool(
            message is not None and message.text == canonical.text_content
        )
        person_matched = bool(
            message is not None
            and len(identities) == 1
            and message.person_id == identities[0].person_id
        )
        classification, should_create = _classify_reply_task_mismatch(
            legacy_count=len(messages),
            task_created_at=[task.created_at for task in tasks],
            last_observed_at=evidence.last_observed_at,
            scope_matched=(
                account_matched
                and project_matched
                and chat_matched
                and business_matched
                and text_matched
            ),
            person_identity_confirmed=person_matched,
        )
        result.append(
            {
                "evidence_id": str(evidence.id),
                "canonical_message_id": str(canonical.id),
                "legacy_message_id": (
                    str(message.id) if message is not None else None
                ),
                "record_ref": _opaque_ref(evidence.id),
                "first_observed_at": _iso(evidence.first_observed_at),
                "last_observed_at": _iso(evidence.last_observed_at),
                "observation_count": evidence.observation_count,
                "canonical_message_exists": True,
                "legacy_message_count": len(messages),
                "reply_task_count": len(tasks),
                "reply_task_state": _reply_task_state(len(tasks)),
                "trigger_matches_message": bool(tasks and message is not None),
                "account_scope_matched": account_matched,
                "project_scope_matched": project_matched,
                "chat_scope_matched": chat_matched,
                "business_connection_scope_matched": business_matched,
                "text_matched": text_matched,
                "verified_identity_count": len(identities),
                "person_identity_confirmed": person_matched,
                "message_should_create_reply_task": should_create,
                "classification": classification,
            }
        )
    return result


async def _verified_identities(
    session: AsyncSession,
    *,
    account_id: UUID,
    platform_user_id: str | None,
) -> list[PersonIdentity]:
    if platform_user_id is None:
        return []
    return list(
        (
            await session.scalars(
                select(PersonIdentity).where(
                    PersonIdentity.account_id == account_id,
                    PersonIdentity.platform == "telegram",
                    PersonIdentity.platform_user_id == platform_user_id,
                    PersonIdentity.is_verified.is_(True),
                )
            )
        ).all()
    )


async def _count_usage(
    session: AsyncSession,
    workspace_id: UUID,
    event_keys: list[str],
) -> int:
    if not event_keys:
        return 0
    return int(
        (
            await session.scalar(
                select(func.count(BillingUsageEventModel.id)).where(
                    BillingUsageEventModel.workspace_id == workspace_id,
                    BillingUsageEventModel.event_key.in_(event_keys),
                )
            )
        )
        or 0
    )


async def _count_ledger(
    session: AsyncSession,
    workspace_id: UUID,
    event_keys: list[str],
) -> int:
    if not event_keys:
        return 0
    return int(
        (
            await session.scalar(
                select(func.count(BillingLedgerEntryModel.id)).where(
                    BillingLedgerEntryModel.workspace_id == workspace_id,
                    BillingLedgerEntryModel.source_event_key.in_(event_keys),
                )
            )
        )
        or 0
    )


def _billing_verdict(
    *,
    reason_code: str,
    original_reservation_exists: bool,
    reservation_count: int,
    usage_count: int,
    ledger_count: int,
) -> str:
    if reason_code != "billing_reservation_replayed":
        return "not_applicable"
    if (
        original_reservation_exists
        and reservation_count == 1
        and usage_count <= 1
        and ledger_count <= 1
    ):
        return "safe_replay_rejected"
    return "ambiguous"


def _transport_verdict(
    *,
    reason_code: str,
    archive_present: bool,
    transcript_complete: bool,
) -> str:
    if reason_code != "openai_transcription_transport_unknown":
        return "not_applicable"
    if archive_present and transcript_complete:
        return "provider_free_projection_recovery_available"
    if archive_present:
        return "residual_provider_outcome_unknown"
    return "residual_archive_missing"


def _classify_reply_task_mismatch(
    *,
    legacy_count: int,
    task_created_at: list[datetime],
    last_observed_at: datetime,
    scope_matched: bool,
    person_identity_confirmed: bool,
) -> tuple[str, bool | None]:
    if (
        legacy_count != 1
        or len(task_created_at) > 1
        or not scope_matched
        or not person_identity_confirmed
    ):
        return "ambiguous", None
    if not task_created_at:
        return "real_defect_missing_reply_task", True
    if task_created_at[0] > last_observed_at:
        return "historical_residue_repaired", True
    return "real_defect_unexpected_reply_task", False


def _message_payload(payload: JsonObject) -> tuple[str, JsonObject]:
    for key in ("business_message", "message"):
        value = payload.get(key)
        if isinstance(value, dict):
            return key, value
    return "unknown", {}


def _counterparty_id(message: JsonObject, direction: str) -> str | None:
    candidate = (
        message.get("from") if direction == "inbound" else message.get("chat")
    )
    if not isinstance(candidate, dict):
        return None
    value = candidate.get("id")
    return str(value) if value is not None else None


def _business_connection(message: JsonObject) -> str | None:
    value = message.get("business_connection_id")
    return value if isinstance(value, str) else None


def _chat_matches(message: JsonObject, chat_id: str) -> bool:
    chat = message.get("chat")
    return bool(isinstance(chat, dict) and str(chat.get("id")) == chat_id)


def _reply_task_state(count: int) -> str:
    if count == 0:
        return "absent"
    if count == 1:
        return "present"
    return "duplicated"


def _opaque_ref(value: UUID) -> str:
    return sha256(str(value).encode()).hexdigest()[:16]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _empty_payload() -> JsonObject:
    return {
        "voice_count": 0,
        "parity_count": 0,
        "voice_records": [],
        "parity_records": [],
        "private_content_exposed": False,
        "provider_identifiers_exposed": False,
        "billing_identifiers_exposed": False,
        "mutation_allowed": False,
        "provider_call_allowed": False,
    }
