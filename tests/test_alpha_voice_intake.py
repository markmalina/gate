"""Pure queue-only orchestration tests for Alpha voice intake."""

import asyncio
from dataclasses import replace

import pytest
from app.application.alpha_access import (
    AlphaAccessFacts,
    AlphaAccessInstallation,
    AlphaContentKind,
    AlphaEventKind,
    AlphaInvocationKind,
    AlphaMessageOrigin,
    AlphaSenderKind,
)
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_voice import (
    MAX_TELEGRAM_VOICE_BYTES,
    AlphaVoiceIntakeCode,
    EvaluateAlphaVoiceCommand,
)
from app.application.alpha_voice_intake import (
    AlphaVoiceIntakeService,
    AlphaVoiceQueueCode,
    AlphaVoiceQueueDisposition,
    AlphaVoiceQueueResult,
    QueueAlphaVoiceJobCommand,
)
from app.application.canonical_messaging import DialogKind


class RecordingQueue:
    def __init__(self, result: AlphaVoiceQueueResult) -> None:
        self.result = result
        self.commands: list[QueueAlphaVoiceJobCommand] = []

    async def enqueue_once(
        self, command: QueueAlphaVoiceJobCommand
    ) -> AlphaVoiceQueueResult:
        self.commands.append(command)
        return self.result


def _access(
    *,
    state: AlphaInstallationState = AlphaInstallationState.ACTIVE,
    invocation: AlphaInvocationKind = AlphaInvocationKind.REPLY_TO_ALPHA,
) -> AlphaAccessFacts:
    installation = AlphaAccessInstallation(
        installation_ref="voice-installation-ref",
        dialog_label="Alpha Group",
        dialog_kind=DialogKind.SUPERGROUP,
        state=state,
        owner_approved=True,
    )
    return AlphaAccessFacts(
        installation=installation,
        dialog_kind=DialogKind.SUPERGROUP,
        event_kind=AlphaEventKind.MESSAGE,
        origin=AlphaMessageOrigin.INBOUND,
        sender_kind=AlphaSenderKind.HUMAN,
        content_kind=AlphaContentKind.VOICE,
        connector_enabled=True,
        invocation_kind=invocation,
    )


def _command(
    *,
    access: AlphaAccessFacts | None = None,
    voice: dict[str, object] | None = None,
) -> EvaluateAlphaVoiceCommand:
    return EvaluateAlphaVoiceCommand(
        workspace_key="workspace-mark",
        canonical_message_id="b182af91-6817-4d14-9791-da6fc6b4ec9e",
        access=access or _access(),
        media_kind="voice",
        voice=voice
        or {
            "file_id": "sensitive-provider-file-id",
            "file_unique_id": "sensitive-provider-unique-id",
            "duration": 42,
            "file_size": 4096,
            "mime_type": "audio/ogg; codecs=opus",
        },
    )


def test_eligible_voice_is_queued_once_without_provider_or_delivery() -> None:
    queue = RecordingQueue(
        AlphaVoiceQueueResult(
            disposition=AlphaVoiceQueueDisposition.QUEUED,
            code=AlphaVoiceQueueCode.QUEUED,
            job_id="queued-job-ref",
        )
    )

    result = asyncio.run(AlphaVoiceIntakeService(queue).intake(_command()))

    assert result.disposition is AlphaVoiceQueueDisposition.QUEUED
    assert result.terminal is False
    assert result.retryable is False
    assert result.duplicate is False
    assert result.job_id == "queued-job-ref"
    assert len(queue.commands) == 1
    assert queue.commands[0].metadata.duration_seconds == 42


def test_store_duplicate_is_an_idempotent_non_retryable_result() -> None:
    queue = RecordingQueue(
        AlphaVoiceQueueResult(
            disposition=AlphaVoiceQueueDisposition.DUPLICATE,
            code=AlphaVoiceQueueCode.DUPLICATE,
            job_id="existing-job-ref",
        )
    )

    result = asyncio.run(AlphaVoiceIntakeService(queue).intake(_command()))

    assert result.disposition is AlphaVoiceQueueDisposition.DUPLICATE
    assert result.duplicate is True
    assert result.terminal is False
    assert result.retryable is False
    assert result.job_id == "existing-job-ref"


@pytest.mark.parametrize(
    ("command", "expected_code"),
    [
        (
            _command(
                voice={
                    "file_id": "file-id",
                    "file_unique_id": "unique-id",
                    "duration": 1,
                    "file_size": "invalid",
                }
            ),
            AlphaVoiceIntakeCode.METADATA_MALFORMED.value,
        ),
        (
            _command(
                voice={
                    "file_id": "file-id",
                    "file_unique_id": "unique-id",
                    "duration": 1,
                    "file_size": MAX_TELEGRAM_VOICE_BYTES + 1,
                }
            ),
            AlphaVoiceIntakeCode.FILE_TOO_LARGE.value,
        ),
        (
            _command(
                access=replace(
                    _access(),
                    invocation_kind=AlphaInvocationKind.NONE,
                )
            ),
            AlphaVoiceIntakeCode.ACCESS_DENIED.value,
        ),
        (
            _command(
                access=_access(state=AlphaInstallationState.PAUSED),
            ),
            AlphaVoiceIntakeCode.ACCESS_DENIED.value,
        ),
    ],
)
def test_invalid_oversize_not_reply_and_inactive_are_terminal_not_exceptions(
    command: EvaluateAlphaVoiceCommand,
    expected_code: str,
) -> None:
    queue = RecordingQueue(
        AlphaVoiceQueueResult(
            disposition=AlphaVoiceQueueDisposition.QUEUED,
            code=AlphaVoiceQueueCode.QUEUED,
            job_id="must-not-be-used",
        )
    )

    result = asyncio.run(AlphaVoiceIntakeService(queue).intake(command))

    assert result.disposition is AlphaVoiceQueueDisposition.TERMINAL
    assert result.code == expected_code
    assert result.terminal is True
    assert result.retryable is False
    assert result.job_id is None
    assert queue.commands == []


def test_queue_objects_hide_provider_identifiers_from_repr() -> None:
    queue = RecordingQueue(
        AlphaVoiceQueueResult(
            disposition=AlphaVoiceQueueDisposition.QUEUED,
            code=AlphaVoiceQueueCode.QUEUED,
            job_id="sensitive-job-id",
        )
    )

    result = asyncio.run(AlphaVoiceIntakeService(queue).intake(_command()))
    command_repr = repr(queue.commands[0])
    result_repr = repr(result)

    assert "sensitive-provider-file-id" not in command_repr
    assert "sensitive-provider-unique-id" not in command_repr
    assert "voice-installation-ref" not in command_repr
    assert "sensitive-job-id" not in result_repr
