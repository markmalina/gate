"""Cross-transport reservations under the same PostgreSQL conversation lock."""

import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.personal_telegram_fence import PersonalSendFenceCommand
from app.application.reply_sends import ReplySendError
from app.infrastructure.personal_telegram_fence import (
    PersonalSendFenceError,
    SqlPersonalTelegramSendFence,
)
from app.infrastructure.reply_sends import SqlTelegramReplySender
from app.models.domain import SendAttempt, SendAttemptStatus
from test_reply_sends_postgres import _postgres_sessions, _seed_reply, _SeededReply, _settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _command(seed: _SeededReply, *, draft_id: UUID, exact_text: str) -> PersonalSendFenceCommand:
    return PersonalSendFenceCommand(
        draft_id=draft_id,
        account_id=seed.account_id,
        actor_ref="actor_mark",
        session_ref="session_synthetic_shared",
        peer_ref="peer_synthetic_shared",
        exact_text_digest=hashlib.sha256(exact_text.encode()).hexdigest(),
        approval_digest=hashlib.sha256(b"synthetic-approval").hexdigest(),
        idempotency_key=f"send_{uuid4().hex}",
        now=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_personal_reservation_blocks_business_and_replays_without_new_attempt() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2, draft_text="Exact shared test")
        fence = SqlPersonalTelegramSendFence(sessions, enabled=True)
        first = _command(seed, draft_id=seed.draft_ids[0], exact_text="Exact shared test")
        reserved = await fence.reserve(first)
        assert reserved.outcome == "unknown" and not reserved.replay
        concurrent = await fence.reserve(first)
        assert concurrent.replay and concurrent.fence_id == reserved.fence_id

        async with sessions() as session:
            sender = SqlTelegramReplySender(session=session, telegram=None, settings=_settings())
            for draft_id in seed.draft_ids:
                with pytest.raises(ReplySendError) as blocked:
                    await sender._raise_if_task_send_blocked(
                        conversation_id=seed.conversation_id,
                        task_id=seed.task_id,
                        draft_id=draft_id,
                    )
                assert blocked.value.code == "task_send_blocked"

        with pytest.raises(PersonalSendFenceError, match="personal_attempt_exists"):
            await fence.reserve(
                _command(seed, draft_id=seed.draft_ids[1], exact_text="Exact shared test")
            )

        finalized = await fence.finalize(
            first, outcome="sent", receipt_ref="telegram_321", external_effect_performed=True
        )
        assert finalized.outcome == "sent"
        # A completed draft cannot be sent a second time via Business.
        async with sessions() as session:
            sender = SqlTelegramReplySender(session=session, telegram=None, settings=_settings())
            with pytest.raises(ReplySendError) as blocked:
                await sender._raise_if_task_send_blocked(
                    conversation_id=seed.conversation_id,
                    task_id=seed.task_id,
                    draft_id=seed.draft_ids[0],
                )
            assert blocked.value.code == "task_send_blocked"


@pytest.mark.anyio
async def test_business_reservation_blocks_personal_before_provider_io() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_count=2, draft_text="Exact shared test")
        async with sessions.begin() as session:
            session.add(
                SendAttempt(
                    reply_draft_id=seed.draft_ids[0],
                    reply_task_id=seed.task_id,
                    conversation_id=seed.conversation_id,
                    account_id=seed.account_id,
                    project_id=seed.project_id,
                    principal_subject="synthetic-business",
                    status=SendAttemptStatus.UNKNOWN.value,
                    account_label="RU",
                    target_chat_id=seed.recipient_chat_id,
                    business_connection_id=seed.business_connection_id,
                )
            )
        fence = SqlPersonalTelegramSendFence(sessions, enabled=True)
        for draft_id in seed.draft_ids:
            with pytest.raises(PersonalSendFenceError, match="business_attempt_exists"):
                await fence.reserve(
                    _command(seed, draft_id=draft_id, exact_text="Exact shared test")
                )


@pytest.mark.anyio
async def test_personal_fence_is_default_off_and_serializes_concurrent_reservations() -> None:
    async with _postgres_sessions() as sessions:
        seed = await _seed_reply(sessions, draft_text="Exact shared test")
        command = _command(seed, draft_id=seed.draft_ids[0], exact_text="Exact shared test")
        with pytest.raises(PersonalSendFenceError, match="shared_fence_disabled"):
            await SqlPersonalTelegramSendFence(sessions).reserve(command)
        fence = SqlPersonalTelegramSendFence(sessions, enabled=True)
        results = await asyncio.gather(fence.reserve(command), fence.reserve(command))
        assert {result.replay for result in results} == {True, False}
        assert results[0].fence_id == results[1].fence_id
