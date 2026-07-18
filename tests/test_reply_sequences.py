from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.application.reply_sequences import (
    QueueApprovedReplySequenceCommand,
    QueueApprovedReplySequenceService,
    QueuedReplySequence,
    ReplySequenceError,
)
from app.core.mcp_security import AccessScope, Principal
from app.worker.reply_sequences import _due_items


ACCOUNT_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("33333333-3333-3333-3333-333333333333")


def _principal() -> Principal:
    return Principal(
        subject="mark",
        auth_method="test",
        scopes=frozenset({"gate:messages:send"}),
        access_scope=AccessScope(
            workspace_id="mark-personal",
            account_ids=frozenset({ACCOUNT_ID}),
            project_ids=frozenset({PROJECT_ID}),
        ),
    )


class RecordingWriter:
    def __init__(self) -> None:
        self.command: QueueApprovedReplySequenceCommand | None = None

    async def queue(
        self, command: QueueApprovedReplySequenceCommand
    ) -> QueuedReplySequence:
        self.command = command
        return SimpleNamespace(sequence_id=uuid4())


@pytest.mark.anyio
async def test_sequence_requires_one_confirmation_and_fixed_minute_interval() -> None:
    writer = RecordingWriter()
    drafts = (uuid4(), uuid4())
    result = await QueueApprovedReplySequenceService(writer).execute(
        QueueApprovedReplySequenceCommand(
            principal=_principal(),
            draft_ids=drafts,
            confirmed=True,
            expected_account=" by ",
            expected_recipient_chat_id=" 227657716 ",
            interval_seconds=60,
        )
    )

    assert result.sequence_id
    assert writer.command is not None
    assert writer.command.draft_ids == drafts
    assert writer.command.expected_account == "BY"
    assert writer.command.expected_recipient_chat_id == "227657716"
    assert writer.command.interval_seconds == 60


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("confirmed", "draft_ids", "interval", "code"),
    [
        (False, (uuid4(), uuid4()), 60, "confirmation_required"),
        (True, (UUID(int=1), UUID(int=1)), 60, "invalid_sequence"),
        (True, (uuid4(), uuid4()), 59, "invalid_interval"),
    ],
)
async def test_sequence_validation_fails_closed(
    confirmed: bool,
    draft_ids: tuple[UUID, ...],
    interval: int,
    code: str,
) -> None:
    with pytest.raises(ReplySequenceError) as raised:
        await QueueApprovedReplySequenceService(RecordingWriter()).execute(
            QueueApprovedReplySequenceCommand(
                principal=_principal(),
                draft_ids=draft_ids,
                confirmed=confirmed,
                expected_account="BY",
                expected_recipient_chat_id="recipient",
                interval_seconds=interval,
            )
        )
    assert raised.value.code == code


def _draft(
    *,
    sequence_id: str,
    position: int,
    scheduled_at: datetime,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        prompt_context={
            "reply_sequence": {
                "id": sequence_id,
                "position": position,
                "size": 2,
                "interval_seconds": 60,
                "scheduled_at": scheduled_at.isoformat(),
                "state": "scheduled",
                "expected_account": "BY",
                "expected_recipient_chat_id": "recipient",
                "principal_subject": "mark",
                "workspace_id": "mark-personal",
            }
        },
    )


def test_due_items_enforce_sixty_seconds_after_confirmed_delivery() -> None:
    now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
    sequence_id = str(uuid4())
    first = _draft(sequence_id=sequence_id, position=0, scheduled_at=now - timedelta(minutes=2))
    second = _draft(sequence_id=sequence_id, position=1, scheduled_at=now - timedelta(minutes=1))
    first_attempt = SimpleNamespace(
        status="sent",
        finished_at=now - timedelta(seconds=59),
    )

    assert _due_items([(first, first_attempt), (second, None)], now=now) == ()
    due = _due_items(
        [(first, first_attempt), (second, None)],
        now=now + timedelta(seconds=1),
    )
    assert len(due) == 1
    assert due[0].draft_id == second.id


def test_due_items_stop_after_ambiguous_attempt() -> None:
    now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
    sequence_id = str(uuid4())
    first = _draft(sequence_id=sequence_id, position=0, scheduled_at=now)
    second = _draft(sequence_id=sequence_id, position=1, scheduled_at=now)
    unknown = SimpleNamespace(status="unknown", finished_at=now)

    assert _due_items([(first, unknown), (second, None)], now=now + timedelta(hours=1)) == ()
