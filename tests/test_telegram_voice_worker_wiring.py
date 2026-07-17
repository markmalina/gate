"""Runtime wiring guards for Telegram Business voice transcription."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.telegram_ingress_identity import TelegramIngressIdentitySnapshot
from app.application.telegram_processing import (
    TelegramProcessingWorkItem,
    TelegramUpdateProcessingError,
)
from app.application.telegram_voice_orchestration import (
    ProcessTelegramVoiceUpdateCommand,
    ProcessTelegramVoiceUpdateResult,
    TelegramVoiceUpdateDisposition,
)
from app.core.config import Settings
from app.worker.telegram_processing import ShadowClassifyingTelegramProcessor


class EmptyIdentityReader:
    async def read(
        self,
        *,
        business_connection_id: str,
    ) -> TelegramIngressIdentitySnapshot | None:
        return None


class ProjectingVoiceUpdates:
    def __init__(self) -> None:
        self.commands: list[ProcessTelegramVoiceUpdateCommand] = []

    async def execute(
        self,
        command: ProcessTelegramVoiceUpdateCommand,
    ) -> ProcessTelegramVoiceUpdateResult:
        self.commands.append(command)
        update: dict[str, Any] = {
            "update_id": command.update["update_id"],
            "business_message": {
                **command.update["business_message"],
                "text": "расшифрованный текст",
                "gate_input_origin": "telegram_voice_transcription",
            },
        }
        update["business_message"].pop("voice")
        return ProcessTelegramVoiceUpdateResult(
            disposition=TelegramVoiceUpdateDisposition.PROJECTED,
            reason_code="telegram_voice_transcribed",
            downstream_update=update,
            downstream_allowed=True,
        )


def voice_item() -> TelegramProcessingWorkItem:
    return TelegramProcessingWorkItem(
        receipt_id="receipt-voice-1",
        outbox_event_id="outbox-voice-1",
        bot_key="gate_m_bot",
        update_id=42,
        payload={
            "update_id": 42,
            "business_message": {
                "message_id": 7,
                "business_connection_id": "private-connection",
                "chat": {"id": "private-chat", "type": "private"},
                "from": {"id": 700000001, "is_bot": False},
                "voice": {
                    "file_id": "private-file",
                    "file_unique_id": "private-unique-file",
                    "file_size": 1024,
                    "duration": 3,
                },
            },
        },
        received_at=datetime(2026, 7, 17, tzinfo=UTC),
        attempt=1,
    )


def test_business_voice_fails_closed_without_runtime_composition() -> None:
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(_env_file=None),
        identity_reader=EmptyIdentityReader(),
        runtime_logger=logging.getLogger("gate.voice.not-configured"),
    )

    with pytest.raises(TelegramUpdateProcessingError) as captured:
        asyncio.run(processor.process(voice_item()))

    assert captured.value.error_code == "telegram_voice_not_configured"


def test_business_voice_is_projected_before_downstream_processing() -> None:
    voice_updates = ProjectingVoiceUpdates()
    processor = ShadowClassifyingTelegramProcessor(
        settings=Settings(
            _env_file=None,
            GATE_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED=True,
        ),
        identity_reader=EmptyIdentityReader(),
        voice_updates=voice_updates,
        runtime_logger=logging.getLogger("gate.voice.projected"),
    )

    asyncio.run(processor.process(voice_item()))

    assert len(voice_updates.commands) == 1
    command = voice_updates.commands[0]
    assert command.workspace_key == "mark-personal"
    assert command.connector_ref == "telegram_business"
    assert command.period_key == "2026-07"
    assert command.requested_microusd == 150
    assert command.enabled is True
