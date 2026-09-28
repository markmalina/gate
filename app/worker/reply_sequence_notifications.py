"""Durable owner-only stop notifications; ambiguous delivery is never replayed."""

import asyncio
import hashlib
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.infrastructure.reply_sequence_owner_route import owner_route
from app.infrastructure.reply_sequence_recipient import recipient_display
from app.infrastructure.reply_sequence_runtime import ReplySequenceRuntime
from app.models.reply_sequences import (
    ReplyDialogGuard,
    ReplySequence,
    ReplySequenceItem,
    ReplySequenceNotification,
)
from app.services.telegram import TelegramApiError, TelegramClient
from app.services.telegram_delivery_gate import CanaryDelivery, canary_delivery


async def notify_once(runtime: ReplySequenceRuntime, telegram: TelegramClient) -> bool:
    if not runtime.settings.gate_reply_sequence_notifications_enabled:
        return False
    async with runtime.sessions() as session:
        now = await runtime.db_now(session)
        # Expired in-flight notifications have an ambiguous external outcome.
        await session.execute(
            update(ReplySequenceNotification)
            .where(
                ReplySequenceNotification.sequence_id.in_(
                    select(ReplySequence.id).where(ReplySequence.workspace_ref == runtime.workspace)
                ),
                ReplySequenceNotification.delivery_state == "sending",
                ReplySequenceNotification.started_at < now - timedelta(seconds=30),
            )
            .values(
                delivery_state="unknown",
                safe_error_code="notification_recovery_unknown",
                finished_at=now,
            )
        )
        event = await session.scalar(
            select(ReplySequenceNotification)
            .join(ReplySequence, ReplySequence.id == ReplySequenceNotification.sequence_id)
            .where(
                ReplySequence.workspace_ref == runtime.workspace,
                ReplySequenceNotification.delivery_state == "pending",
                ReplySequenceNotification.next_attempt_at <= now,
            )
            .order_by(ReplySequenceNotification.next_attempt_at, ReplySequenceNotification.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=ReplySequenceNotification)
        )
        if event is None:
            await session.commit()
            return False
        sequence = await session.get(ReplySequence, event.sequence_id)
        assert sequence is not None
        guard = await session.get(ReplyDialogGuard, sequence.guard_id)
        route = (
            await owner_route(
                session,
                account_id=sequence.account_id,
                principal_subject=sequence.principal_subject,
                recipient=guard.provider_chat_id,
            )
            if guard
            else None
        )
        if route is None:
            event.delivery_state = "blocked_route"
            event.safe_error_code = "owner_route_unverified"
            await session.commit()
            return True
        assert guard is not None
        items = (
            await session.scalars(
                select(ReplySequenceItem).where(ReplySequenceItem.sequence_id == sequence.id)
            )
        ).all()
        counts = {
            state: sum(i.state == state for i in items)
            for state in ("sent", "cancelled", "unknown", "sending")
        }
        display = await recipient_display(
            session,
            account_id=sequence.account_id,
            project_id=sequence.project_id,
            conversation_id=sequence.conversation_id,
            chat_id=guard.provider_chat_id,
        )
        text = (
            f"Серия {str(sequence.id)[:8]} ({sequence.account_label}) "
            f"адресату «{display}» остановлена. "
            f"{now.astimezone(ZoneInfo('Europe/Moscow')):%d.%m.%Y %H:%M} МСК. "
            f"Отправлено: {counts['sent']}/{sequence.item_count}; отменено: {counts['cancelled']}; "
            f"неопределённо/в процессе: {counts['unknown'] + counts['sending']}. "
            f"Причина: {sequence.stop_reason}. Автоматически не продолжится."
        )
        event.delivery_state = "sending"
        event.started_at = now
        event.attempts += 1
        event.owner_route_ref = route.id
        event_id, destination = event.id, str(route.user_chat_id)
        principal_subject = sequence.principal_subject
        await session.commit()
    outcome = "unknown"
    error: str | None = "notification_delivery_unknown"
    message_id: str | None = None
    retry_after: int | None = None
    try:
        async with asyncio.timeout(20):
            with canary_delivery(
                CanaryDelivery(
                    kind="notification",
                    principal_subject=principal_subject,
                    account_id=None,
                    dialog_ref=None,
                    body_sha256=None,
                    destination=destination,
                    notification_template="reply_sequence_stop_v1",
                    notification_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            ):
                response = await telegram.send_plain_message(chat_id=destination, text=text)
        result = response.get("result")
        if (
            response.get("ok") is True
            and isinstance(result, dict)
            and type(result.get("message_id")) is int
        ):
            outcome, error, message_id = "sent", None, str(result["message_id"])
    except TelegramApiError as exc:
        if exc.error_code == 429:
            outcome, error, retry_after = (
                "retry",
                "notification_rate_limited",
                exc.retry_after_seconds,
            )
        elif exc.error_code == 403 and exc.http_status is None:
            outcome, error = "failed", "canary_admission_closed"
        elif exc.http_status is not None and exc.http_status < 500:
            outcome, error = "failed", "notification_provider_rejected"
    except Exception:
        pass
    async with runtime.sessions() as session:
        event = await session.scalar(
            select(ReplySequenceNotification)
            .where(ReplySequenceNotification.id == event_id)
            .with_for_update()
        )
        assert event is not None
        if event.delivery_state != "sending":
            return True
        now = await runtime.db_now(session)
        if outcome == "retry" and event.attempts < 4:
            delay = max(
                (5, 30, 120)[event.attempts - 1], retry_after if retry_after is not None else 60
            )
            try:
                event.next_attempt_at = now + timedelta(seconds=delay)
                event.delivery_state = "pending"
            except OverflowError:
                event.delivery_state = "blocked_route"
                error = "notification_cooldown_unrepresentable"
        else:
            event.delivery_state = "failed" if outcome == "retry" else outcome
            event.finished_at = now
        event.safe_error_code = error
        event.provider_message_ref = message_id
        await session.commit()
    return True


async def run_notification_loop(runtime: ReplySequenceRuntime, telegram: TelegramClient) -> None:
    while True:
        try:
            await notify_once(runtime, telegram)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Stop is already committed; notifier failures cannot resume delivery.
            pass
        await asyncio.sleep(runtime.settings.gate_reply_sequence_poll_seconds)
