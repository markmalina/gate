from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator
from app.api.mcp_routes import _gate_queue_approved_reply_sequence_tool
from app.api.mcp_schemas import GATE_SEQUENCE_OUTPUT_SCHEMA
from app.application.reply_sequences import (
    MAX_SEQUENCE_DRAFTS,
    QueueApprovedReplySequenceCommand,
    QueueApprovedReplySequenceService,
    QueuedReplySequence,
    ReplySequenceError,
)
from app.core.config import Settings
from app.core.mcp_security import AccessScope, Principal
from app.infrastructure.reply_sequences import SqlReplySequenceWriter
from app.worker.reply_sequences import (
    _due_items,
    _sequence_row_from_scalars,
    _SequenceRow,
)

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
@pytest.mark.parametrize("size", [2, 20, 21, 49, 50])
async def test_supported_sequence_sizes_preserve_input_order(size: int) -> None:
    writer = RecordingWriter()
    ids = tuple(uuid4() for _ in range(size))
    await QueueApprovedReplySequenceService(writer).execute(
        QueueApprovedReplySequenceCommand(
            principal=_principal(),
            draft_ids=ids,
            confirmed=True,
            expected_account="RU",
            expected_recipient_chat_id="recipient",
        )
    )
    assert writer.command is not None
    assert writer.command.draft_ids == ids


@pytest.mark.anyio
@pytest.mark.parametrize("size", [0, 1, 51])
async def test_invalid_sizes_fail_before_writer_or_sql(size: int) -> None:
    ids = tuple(uuid4() for _ in range(size))
    command = QueueApprovedReplySequenceCommand(
        principal=_principal(),
        draft_ids=ids,
        confirmed=True,
        expected_account="RU",
        expected_recipient_chat_id="recipient",
    )
    writer = RecordingWriter()
    with pytest.raises(ReplySequenceError, match="2 to 50"):
        await QueueApprovedReplySequenceService(writer).execute(command)
    assert writer.command is None

    class NoSqlSession:
        async def execute(self, *args: object) -> None:
            raise AssertionError("Invalid size reached SQL")

    with pytest.raises(ReplySequenceError, match="2 to 50"):
        await SqlReplySequenceWriter(NoSqlSession()).queue(command)  # type: ignore[arg-type]


def test_mcp_sequence_input_output_and_description_share_bounds() -> None:
    descriptor = _gate_queue_approved_reply_sequence_tool(Settings(_env_file=None))
    input_items = descriptor["inputSchema"]["properties"]["draft_ids"]
    output_items = GATE_SEQUENCE_OUTPUT_SCHEMA["oneOf"][0]["properties"]["items"]
    assert input_items["minItems"] == output_items["minItems"] == 2
    assert input_items["maxItems"] == output_items["maxItems"] == MAX_SEQUENCE_DRAFTS == 50
    assert "2 to 50" in descriptor["description"]

    payload = {
        "sequence_id": str(uuid4()),
        "sequence_ref": "12345678",
        "task_id": str(uuid4()),
        "account_id": str(ACCOUNT_ID),
        "project_id": str(PROJECT_ID),
        "account": {"alias": "test_ru", "label": "RU"},
        "recipient_chat_id": "recipient",
        "interval_seconds": 60,
        "queued": True,
        "sent": False,
        "items": [
            {
                "draft_id": str(uuid4()),
                "position": position,
                "scheduled_at": "2026-09-27T18:00:00+00:00",
            }
            for position in range(50)
        ],
    }
    validator = Draft202012Validator(GATE_SEQUENCE_OUTPUT_SCHEMA)
    assert validator.is_valid(payload)
    payload["items"].append(
        {
            "draft_id": str(uuid4()),
            "position": 50,
            "scheduled_at": "2026-09-27T18:50:00+00:00",
        }
    )
    assert not validator.is_valid(payload)


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


def _row(
    *,
    sequence_id: str,
    position: int,
    scheduled_at: datetime,
    attempt_status: str | None = None,
    attempt_finished_at: datetime | None = None,
) -> _SequenceRow:
    return _SequenceRow(
        draft_id=uuid4(),
        task_id=uuid4(),
        conversation_id=uuid4(),
        sequence_id=sequence_id,
        position=position,
        state="scheduled",
        scheduled_at=scheduled_at,
        interval_seconds=60,
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        expected_account="BY",
        expected_recipient_chat_id="recipient",
        principal_subject="mark",
        workspace_id="mark-personal",
        attempt_status=attempt_status,
        attempt_finished_at=attempt_finished_at,
    )


def test_due_items_enforce_sixty_seconds_after_confirmed_delivery() -> None:
    now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
    sequence_id = str(uuid4())
    first = _row(
        sequence_id=sequence_id,
        position=0,
        scheduled_at=now - timedelta(minutes=2),
        attempt_status="sent",
        attempt_finished_at=now - timedelta(seconds=59),
    )
    second = _row(
        sequence_id=sequence_id,
        position=1,
        scheduled_at=now - timedelta(minutes=1),
    )

    assert _due_items((first, second), now=now) == ()
    due = _due_items((first, second), now=now + timedelta(seconds=1))
    assert len(due) == 1
    assert due[0].draft_id == second.draft_id


def test_due_items_stop_after_ambiguous_attempt() -> None:
    now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
    sequence_id = str(uuid4())
    first = _row(
        sequence_id=sequence_id,
        position=0,
        scheduled_at=now,
        attempt_status="unknown",
        attempt_finished_at=now,
    )
    second = _row(sequence_id=sequence_id, position=1, scheduled_at=now)

    assert _due_items((first, second), now=now + timedelta(hours=1)) == ()


def test_fifty_item_worker_progress_uses_confirmed_completion_not_original_schedule() -> None:
    queued_at = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)
    sequence_id = str(uuid4())
    rows = tuple(
        _row(
            sequence_id=sequence_id,
            position=position,
            scheduled_at=queued_at + timedelta(minutes=position),
            attempt_status="sent" if position < 49 else None,
            attempt_finished_at=(
                queued_at + timedelta(minutes=position, seconds=30)
                if position < 49
                else None
            ),
        )
        for position in range(50)
    )
    assert _due_items(rows, now=queued_at + timedelta(minutes=49, seconds=29)) == ()
    due = _due_items(rows, now=queued_at + timedelta(minutes=49, seconds=30))
    assert len(due) == 1
    assert due[0].draft_id == rows[49].draft_id
    assert due[0].position == 49


@pytest.mark.parametrize("status", ["failed", "unknown", "sending"])
def test_fifty_item_worker_stops_after_non_sent_attempt(status: str) -> None:
    now = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)
    sequence_id = str(uuid4())
    rows = tuple(
        _row(
            sequence_id=sequence_id,
            position=position,
            scheduled_at=now,
            attempt_status="sent" if position < 25 else status if position == 25 else None,
            attempt_finished_at=now if position <= 25 else None,
        )
        for position in range(50)
    )
    assert _due_items(rows, now=now + timedelta(hours=2)) == ()


def test_scalar_projection_is_independent_from_mutated_source_metadata() -> None:
    now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
    metadata: dict[str, object] = {
        "reply_sequence": {
            "id": "sequence-1",
            "position": 0,
            "interval_seconds": 60,
            "scheduled_at": now.isoformat(),
            "state": "scheduled",
            "expected_account": "BY",
            "expected_recipient_chat_id": "recipient",
            "principal_subject": "mark",
            "workspace_id": "mark-personal",
        }
    }
    row = _sequence_row_from_scalars(
        draft_id=uuid4(),
        task_id=uuid4(),
        conversation_id=uuid4(),
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
        prompt_context=metadata,
        attempt_status=None,
        attempt_finished_at=None,
    )

    assert row is not None
    sequence = metadata["reply_sequence"]
    assert isinstance(sequence, dict)
    sequence["state"] = "cancelled"
    sequence["position"] = 99

    assert row.state == "scheduled"
    assert row.position == 0
    assert len(_due_items((row,), now=now)) == 1
