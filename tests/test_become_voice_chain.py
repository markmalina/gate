from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module

import pytest

chain = import_module("app.application.become_voice_chain")
NOW = datetime(2026, 8, 2, tzinfo=UTC)
SOURCE_SHA = "a" * 40


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def prepare(self, **values: object) -> object:
        self.calls.append(("prepare", values))
        return chain.PreparedBecomeVoiceCorrelation(
            correlation_ref="synthetic://correlation/voice/123",
            slot_count=4,
            duplicate=False,
        )

    async def confirm_transcript(self, **values: object) -> object:
        self.calls.append(("confirm", values))
        return chain.ConfirmedBecomeTranscriptReceipt(
            correlation_ref="synthetic://correlation/voice/123",
            transcript_digest="b" * 64,
            transcript_expires_at=datetime(2026, 9, 1, tzinfo=UTC),
            relations_slot_state=chain.BecomeChainSlotState.READY,
            duplicate=False,
        )

    async def reserve_stage(self, **values: object) -> object:
        self.calls.append(("reserve", values))
        payload = values["request_payload"]
        assert isinstance(payload, dict)
        return chain.ReservedBecomeChainStage(
            correlation_ref="synthetic://correlation/voice/123",
            stage=chain.BecomeChainStage.RELATIONS,
            state=chain.BecomeChainSlotState.REQUESTED,
            request_digest=chain.canonical_payload_digest(payload),
            source_sha=SOURCE_SHA,
            duplicate=False,
            reconciliation_required=True,
            request_payload=payload,
        )

    async def finalize_stage(self, **values: object) -> object:
        self.calls.append(("finalize", values))
        return chain.FinalizedBecomeChainStage(
            correlation_ref="synthetic://correlation/voice/123",
            stage=chain.BecomeChainStage.RELATIONS,
            state=chain.BecomeChainSlotState.COMPLETED,
            request_digest="c" * 64,
            receipt_digest="d" * 64,
            duplicate=False,
        )


@pytest.mark.anyio
async def test_prepare_creates_content_free_root_contract() -> None:
    store = FakeStore()
    result = await chain.PrepareBecomeVoiceCorrelationService(
        store
    ).execute(
        workspace_key="workspace-test",
        intake_key="become_media_123",
        prepared_at=NOW,
    )
    assert result.slot_count == 4
    assert result.correlation_ref.startswith("synthetic://correlation/voice/")
    assert store.calls[0][0] == "prepare"


@pytest.mark.anyio
async def test_confirm_returns_no_transcript_content() -> None:
    store = FakeStore()
    result = await chain.ConfirmBecomeTranscriptService(store).execute(
        workspace_key="workspace-test",
        correlation_ref="synthetic://correlation/voice/123",
        transcript="confirmed private words",
        confirmed_at=NOW,
    )
    assert result.transcript is None
    assert result.transcript_digest == "b" * 64
    assert result.relations_slot_state is chain.BecomeChainSlotState.READY


def test_content_free_receipt_rejects_transcript() -> None:
    with pytest.raises(
        ValueError,
        match="become_transcript_receipt_must_be_content_free",
    ):
        chain.ConfirmedBecomeTranscriptReceipt(
            correlation_ref="synthetic://correlation/voice/123",
            transcript_digest="b" * 64,
            transcript_expires_at=NOW,
            relations_slot_state=chain.BecomeChainSlotState.READY,
            duplicate=False,
            transcript="private words",
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sensitive_key",
    [
        "raw_text",
        "transcript_text",
        "provider_payload",
        "media_bytes",
        "auth_token",
        "customer_email",
        "personal-data",
        "nested_pii_value",
    ],
)
async def test_reserve_rejects_normalized_sensitive_keys(
    sensitive_key: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="become_chain_payload_contains_private_content",
    ):
        await chain.ReserveBecomeChainStageService(FakeStore()).execute(
            workspace_key="workspace-test",
            correlation_ref="synthetic://correlation/voice/123",
            stage=chain.BecomeChainStage.RELATIONS,
            request_payload={"safe": [{sensitive_key: "private"}]},
            source_sha=SOURCE_SHA,
            requested_at=NOW,
        )


@pytest.mark.anyio
async def test_reserve_rejects_private_content() -> None:
    with pytest.raises(
        ValueError,
        match="become_chain_payload_contains_private_content",
    ):
        await chain.ReserveBecomeChainStageService(FakeStore()).execute(
            workspace_key="workspace-test",
            correlation_ref="synthetic://correlation/voice/123",
            stage=chain.BecomeChainStage.RELATIONS,
            request_payload={"transcript": "private words"},
            source_sha=SOURCE_SHA,
            requested_at=NOW,
        )


@pytest.mark.anyio
async def test_reserved_request_is_reconciliation_only_until_receipt() -> None:
    payload = {
        "schema": "become.i4-service-request@1",
        "correlation_ref": "synthetic://correlation/voice/123",
        "consent_receipt_ref": "consent_123",
    }
    result = await chain.ReserveBecomeChainStageService(FakeStore()).execute(
        workspace_key="workspace-test",
        correlation_ref="synthetic://correlation/voice/123",
        stage=chain.BecomeChainStage.RELATIONS,
        request_payload=payload,
        source_sha=SOURCE_SHA,
        requested_at=NOW,
    )
    assert result.state is chain.BecomeChainSlotState.REQUESTED
    assert result.reconciliation_required is True
    assert result.receipt_payload is None


def test_take_mapping_is_deterministic_and_directional() -> None:
    first = chain.take_mapping_digest(
        oko_result_receipt_ref="oko_receipt_1",
        take_source_asset_ref="asset_1",
    )
    second = chain.take_mapping_digest(
        oko_result_receipt_ref="oko_receipt_1",
        take_source_asset_ref="asset_1",
    )
    changed = chain.take_mapping_digest(
        oko_result_receipt_ref="oko_receipt_2",
        take_source_asset_ref="asset_1",
    )
    assert first == second
    assert first != changed
    assert len(first) == 64
