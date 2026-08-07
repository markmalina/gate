from datetime import UTC, datetime

import pytest

from app.application.beta_wedding_voice import (
    BetaWeddingPhaseReservation,
    BetaWeddingVoiceDisposition,
    BetaWeddingVoiceJob,
    BetaWeddingVoicePhase,
    ProcessBetaWeddingVoice,
)


class Store:
    def __init__(self) -> None:
        self.job = BetaWeddingVoiceJob(
            job_id="voice-job",
            telegram_chat_id="chat",
            telegram_file_id="file",
            sender_label="Марк",
            occurred_at=datetime(2026, 8, 7, 8, 0, tzinfo=UTC),
            duration_seconds=42,
        )
        self.steps: list[str] = []
        self.phase = 0

    async def reserve_voice_receipt(self, *, receipt_id: str, now: datetime):
        del receipt_id, now
        return self.job, BetaWeddingPhaseReservation(True, "receipt", "chat", "receipt")

    async def claim_phase(
        self, *, job_id: str, phase: BetaWeddingVoicePhase, now: datetime
    ):
        del job_id, now
        self.steps.append(f"claim:{phase}")
        if phase is BetaWeddingVoicePhase.STRUCTURED_RESULT:
            return BetaWeddingPhaseReservation(True, "result", "chat", "structured")
        return None

    async def settle_phase(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        error_code: str | None,
        now: datetime,
    ) -> None:
        del telegram_message_id, error_code, now
        self.steps.append(f"settle:{delivery_id}:{outcome}")

    async def claim_transcription(self, *, job_id: str, now: datetime):
        del job_id, now
        self.steps.append("claim:transcription")
        return self.job

    async def persist_structured_result(
        self, *, job_id: str, transcript: str, structured_text: str, now: datetime
    ) -> bool:
        del job_id, transcript, structured_text, now
        self.steps.append("persist:structured")
        return True

    async def settle_transcription_failure(
        self, *, job_id: str, error_code: str, now: datetime
    ) -> None:
        del job_id, error_code, now
        self.steps.append("transcription:failed")


class Telegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_plain_message(self, *, chat_id: str, text: str):
        assert chat_id == "chat"
        self.sent.append(text)
        return {"result": {"message_id": len(self.sent)}}

    async def download_voice(self, *, file_id: str) -> bytes:
        assert file_id == "file"
        return b"ogg"


class Transcriber:
    async def transcribe(self, *, audio: bytes) -> str:
        assert audio == b"ogg"
        return "Нужна площадка на сорок гостей."


class Structurer:
    async def structure(self, *, transcript: str) -> str:
        return "Я поняла так:\n- Факты: " + transcript


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_voice_persists_structure_before_structured_delivery() -> None:
    store = Store()
    telegram = Telegram()
    result = await ProcessBetaWeddingVoice(
        store=store,
        telegram=telegram,
        transcriber=Transcriber(),
        structurer=Structurer(),
    ).execute(receipt_id="receipt", now=datetime(2026, 8, 7, 8, 1, tzinfo=UTC))

    assert result.disposition is BetaWeddingVoiceDisposition.SENT
    assert store.steps == [
        "settle:receipt:sent",
        "claim:transcription",
        "persist:structured",
        "claim:structured_result",
        "settle:result:sent",
    ]
    assert telegram.sent == ["receipt", "structured"]
