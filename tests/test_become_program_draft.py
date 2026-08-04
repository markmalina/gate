"""Unit contract proofs for the owner-only Slice 2 PROGRAM draft."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from app.application.become_controlled_intake import BecomeControlledIntakeError
from app.application.become_program_draft import (  # noqa: I001
    DeliverPilotProgramDraft,
    ProcessConfirmedTranscriptToProgramDraft,
    ProgramDeliveryJob,
    ProgramDraftDisposition,
    ProgramDraftJob,
    ProgramDraftProviderResult,
    ProgramDraftSource,
    parse_program_callback_data,
    program_delivery_body,
    program_digest,
)

NOW = datetime(2026, 8, 4, 5, 0, tzinfo=UTC)


@dataclass
class FakeStore:
    source: ProgramDraftSource | None
    claimed: bool = False
    completed: int = 0
    unknown: int = 0
    settled: list[str] = field(default_factory=list)

    async def reserve_from_confirmed_transcript(
        self, *, workspace_key: str, now: datetime
    ) -> ProgramDraftSource | None:
        assert workspace_key == "mark-personal"
        return self.source

    async def claim_provider_call(
        self, *, draft_id: str, now: datetime
    ) -> ProgramDraftJob | None:
        if self.claimed or self.source is None:
            return None
        self.claimed = True
        return ProgramDraftJob(draft_id, self.source.transcript_text)

    async def complete_provider_call(self, **kwargs: object) -> None:
        self.completed += 1

    async def mark_provider_unknown(self, **kwargs: object) -> None:
        self.unknown += 1

    async def apply_callback(self, **kwargs: object) -> ProgramDraftDisposition:
        return ProgramDraftDisposition.APPROVED

    async def apply_correction(self, **kwargs: object) -> bool:
        return True

    async def claim_delivery(
        self, *, now: datetime, max_operations: int
    ) -> ProgramDeliveryJob | None:
        if self.settled:
            return None
        return ProgramDeliveryJob(
            delivery_id="d",
            kind="presentation",
            provider_dialog_id="chat",
            version=1,
            program_digest="a" * 64,
            callback_ref="1" * 32,
            program_text="# Программа",
        )

    async def settle_delivery(self, **kwargs: object) -> None:
        self.settled.append(str(kwargs["outcome"]))

    async def purge_expired(self, *, now: datetime) -> int:
        return 0


class FakeProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def generate(
        self, *, confirmed_transcript: str
    ) -> ProgramDraftProviderResult:
        self.calls += 1
        assert confirmed_transcript == "подтвержденный текст"
        if self.fail:
            raise TimeoutError("private provider detail")
        return ProgramDraftProviderResult("# Программа\n\n1. Шаг", 10, 20)


class FakeTelegram:
    def __init__(self, *, ambiguous: bool = False) -> None:
        self.calls = 0
        self.ambiguous = ambiguous

    async def send_message(self, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        if self.ambiguous:
            raise TimeoutError
        return {"result": {"message_id": 42}}

    async def answer_callback_query(self, **kwargs: object) -> dict[str, object]:
        return {"ok": True}


def source() -> ProgramDraftSource:
    return ProgramDraftSource(
        draft_id="00000000-0000-0000-0000-000000000001",
        transcript_review_id="00000000-0000-0000-0000-000000000002",
        transcript_version=2,
        transcript_digest="a" * 64,
        transcript_text="подтвержденный текст",
    )


@pytest.mark.anyio
async def test_default_off_never_calls_provider() -> None:
    store = FakeStore(source())
    provider = FakeProvider()
    result = await ProcessConfirmedTranscriptToProgramDraft(
        store=store,
        provider=provider,
        workspace_key="mark-personal",
        provider_model="model",
        enabled=False,
        provider_max_calls=0,
    ).run_once(now=NOW)
    assert result is ProgramDraftDisposition.DISABLED
    assert provider.calls == 0
    assert store.completed == 0


@pytest.mark.anyio
async def test_exact_source_generates_once_and_restart_replays() -> None:
    store = FakeStore(source())
    provider = FakeProvider()
    service = ProcessConfirmedTranscriptToProgramDraft(
        store=store,
        provider=provider,
        workspace_key="mark-personal",
        provider_model="model",
        enabled=True,
        provider_max_calls=1,
    )
    assert await service.run_once(now=NOW) is ProgramDraftDisposition.GENERATED
    assert await service.run_once(now=NOW) is ProgramDraftDisposition.REPLAYED
    assert provider.calls == 1
    assert store.completed == 1


@pytest.mark.anyio
async def test_unknown_provider_outcome_is_terminal_without_retry() -> None:
    store = FakeStore(source())
    provider = FakeProvider(fail=True)
    service = ProcessConfirmedTranscriptToProgramDraft(
        store=store,
        provider=provider,
        workspace_key="mark-personal",
        provider_model="model",
        enabled=True,
        provider_max_calls=1,
    )
    assert await service.run_once(now=NOW) is ProgramDraftDisposition.UNKNOWN
    assert await service.run_once(now=NOW) is ProgramDraftDisposition.REPLAYED
    assert provider.calls == 1
    assert store.unknown == 1


@pytest.mark.anyio
async def test_delivery_unknown_is_not_blindly_retried() -> None:
    store = FakeStore(source())
    telegram = FakeTelegram(ambiguous=True)
    delivery = DeliverPilotProgramDraft(
        store=store,
        telegram=telegram,
        enabled=True,
        max_operations=4,
    )
    assert await delivery.run_once(now=NOW) == "unknown"
    assert await delivery.run_once(now=NOW) == "idle"
    assert telegram.calls == 1
    assert store.settled == ["unknown"]


def test_callback_namespace_and_delivery_are_program_specific() -> None:
    assert parse_program_callback_data("b2:c:" + "1" * 32) == (
        "c",
        "1" * 32,
    )
    with pytest.raises(BecomeControlledIntakeError):
        parse_program_callback_data("b1:c:" + "1" * 32)
    text, markup = program_delivery_body(
        ProgramDeliveryJob(
            delivery_id="d",
            kind="presentation",
            provider_dialog_id="chat",
            version=1,
            program_digest=program_digest("# Программа"),
            callback_ref="1" * 32,
            program_text="# Программа",
        )
    )
    assert text.startswith("Черновик программы:")
    assert markup is not None
    assert markup["inline_keyboard"][0][0]["callback_data"].startswith("b2:c:")
