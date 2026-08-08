from datetime import UTC, datetime

import pytest
from app.application.beta_material import (
    BetaMaterialDisposition,
    BetaMaterialJob,
    BetaMaterialPhase,
    BetaMaterialPhaseReservation,
    ProcessBetaMaterial,
)


class Store:
    def __init__(self, *, media_kind: str = "voice", duplicate: bool = False) -> None:
        self.job = BetaMaterialJob(
            job_id="job",
            telegram_chat_id="chat",
            telegram_file_id="file",
            media_kind=media_kind,
            sender_label="Марк",
            occurred_at=datetime(2026, 8, 8, 8, 0, tzinfo=UTC),
            file_name="plan.txt" if media_kind == "document" else None,
            mime_type="text/plain" if media_kind == "document" else "audio/ogg",
            file_size=7,
            duration_seconds=12 if media_kind == "voice" else None,
        )
        self.duplicate = duplicate
        self.steps: list[str] = []
        self.persisted: tuple[str, str, str] | None = None

    async def reserve_material_receipt(self, *, receipt_id: str, now: datetime):
        del receipt_id, now
        return self.job, BetaMaterialPhaseReservation(
            not self.duplicate, "receipt", "chat", "receipt"
        )

    async def claim_processing(self, *, job_id: str, now: datetime):
        del job_id, now
        self.steps.append("claim:processing")
        return self.job

    async def persist_result(
        self,
        *,
        job_id: str,
        source_file_sha256: str,
        extracted_text: str,
        structured_text: str,
        now: datetime,
    ) -> bool:
        del job_id, now
        self.steps.append("persist:result")
        self.persisted = (
            source_file_sha256,
            extracted_text,
            structured_text,
        )
        return True

    async def settle_processing_failure(
        self, *, job_id: str, error_code: str, now: datetime
    ):
        del job_id, now
        self.steps.append(f"failed:{error_code}")
        return BetaMaterialPhaseReservation(
            True, "failure", "chat", "safe failure"
        )

    async def claim_phase(
        self, *, job_id: str, phase: BetaMaterialPhase, now: datetime
    ):
        del job_id, now
        self.steps.append(f"claim:{phase.value}")
        return BetaMaterialPhaseReservation(
            True, "result", "chat", "structured"
        )

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


class Telegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_plain_message(self, *, chat_id: str, text: str):
        assert chat_id == "chat"
        self.sent.append(text)
        return {"result": {"message_id": len(self.sent)}}

    async def download_file(self, *, file_id: str) -> bytes:
        assert file_id == "file"
        return b"content"


class Extractor:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.kinds: list[str] = []

    async def extract(self, *, job: BetaMaterialJob, content: bytes) -> str:
        assert content == b"content"
        self.kinds.append(job.media_kind)
        if self.fails:
            raise RuntimeError("beta_material_format_unsupported")
        return "Извлечённый текст"


class Structurer:
    async def structure(
        self, *, media_kind: str, extracted_text: str
    ) -> str:
        return f"Я поняла так: {media_kind}: {extracted_text}"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("media_kind", ["voice", "document", "photo"])
async def test_each_material_is_persisted_before_result_delivery(
    media_kind: str,
) -> None:
    store = Store(media_kind=media_kind)
    telegram = Telegram()
    extractor = Extractor()

    result = await ProcessBetaMaterial(
        store=store,
        telegram=telegram,
        extractor=extractor,
        structurer=Structurer(),
    ).execute(
        receipt_id="receipt",
        now=datetime(2026, 8, 8, 8, 1, tzinfo=UTC),
    )

    assert result.disposition is BetaMaterialDisposition.SENT
    assert extractor.kinds == [media_kind]
    assert store.persisted is not None
    assert len(store.persisted[0]) == 64
    assert store.steps == [
        "settle:receipt:sent",
        "claim:processing",
        "persist:result",
        "claim:structured_result",
        "settle:result:sent",
    ]
    assert telegram.sent == ["receipt", "structured"]


@pytest.mark.anyio
async def test_duplicate_material_has_no_download_provider_or_send() -> None:
    store = Store(duplicate=True)
    telegram = Telegram()
    extractor = Extractor()

    result = await ProcessBetaMaterial(
        store=store,
        telegram=telegram,
        extractor=extractor,
        structurer=Structurer(),
    ).execute(
        receipt_id="receipt",
        now=datetime(2026, 8, 8, 8, 1, tzinfo=UTC),
    )

    assert result.disposition is BetaMaterialDisposition.DUPLICATE
    assert extractor.kinds == []
    assert telegram.sent == []
    assert store.steps == []


@pytest.mark.anyio
async def test_safe_processing_failure_is_persisted_and_reported() -> None:
    store = Store(media_kind="document")
    telegram = Telegram()

    result = await ProcessBetaMaterial(
        store=store,
        telegram=telegram,
        extractor=Extractor(fails=True),
        structurer=Structurer(),
    ).execute(
        receipt_id="receipt",
        now=datetime(2026, 8, 8, 8, 1, tzinfo=UTC),
    )

    assert result.disposition is BetaMaterialDisposition.SENT
    assert "failed:beta_material_format_unsupported" in store.steps
    assert telegram.sent == ["receipt", "safe failure"]
