"""AQUB live Gate→runtime contract tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from app.application.aqub_food_media import AQUBDownloadedMedia
from app.application.aqub_live import (
    AQUB_ALBUM_TEXT_GRACE_SECONDS,
    AQUB_PROGRAM_PROMPT_TEXT,
    AQUBLiveIngressAdapter,
    AQUBRuntimeUnavailable,
    HttpAQUBRuntimeClient,
    forward_aqub_album_result,
)
from app.application.aqub_transport import (
    AQUBDurableAcceptance,
    AQUBPhotoReference,
    AQUBReceiptStatus,
    AQUBStoreDisposition,
    AQUBTransportBinding,
    AQUBTransportCommand,
    AQUBTransportContentKind,
    AQUBTransportInbound,
)

SIGNING_KEY = b"0123456789abcdef0123456789abcdef-live-test"
READY_AT = datetime(2026, 8, 7, 13, 17, tzinfo=UTC)
TEST_CHAT_ID = "-1001234567890"
TEST_OTHER_CHAT_ID = "-1001234567891"
TEST_ALPHA_ID = "123456789"
TEST_CANDIDATE_ID = "987654321"


class Store:
    def __init__(self) -> None:
        self.commands: list[AQUBTransportCommand] = []
        self._created: AQUBDurableAcceptance | None = None

    async def accept(self, command: AQUBTransportCommand) -> AQUBDurableAcceptance:
        self.commands.append(command)
        if self._created is not None:
            return AQUBDurableAcceptance(
                status=AQUBReceiptStatus.COMMITTED,
                disposition=AQUBStoreDisposition.DUPLICATE,
                command_receipt_id=self._created.command_receipt_id,
                root_event_id=self._created.root_event_id,
                correlation_id=self._created.correlation_id,
                causation_id=self._created.causation_id,
            )
        self._created = AQUBDurableAcceptance(
            status=AQUBReceiptStatus.COMMITTED,
            disposition=AQUBStoreDisposition.CREATED,
            command_receipt_id="raw-receipt-id",
            root_event_id="raw-root-id",
            correlation_id="raw-correlation-id",
            causation_id="raw-causation-id",
        )
        return self._created


class UncommittedStore(Store):
    async def accept(self, command: AQUBTransportCommand) -> AQUBDurableAcceptance:
        self.commands.append(command)
        return AQUBDurableAcceptance(
            status=cast(AQUBReceiptStatus, "received"),
            disposition=AQUBStoreDisposition.CREATED,
            command_receipt_id="raw-receipt-id",
            root_event_id="raw-root-id",
            correlation_id="raw-correlation-id",
            causation_id="raw-causation-id",
        )


class Runtime:
    def __init__(self) -> None:
        self.raw_bodies: list[bytes] = []
        self.signatures: list[str] = []

    async def post_envelope(
        self,
        *,
        raw_body: bytes,
        signature: str,
    ) -> dict[str, object]:
        self.raw_bodies.append(raw_body)
        self.signatures.append(signature)
        return {
            "ok": True,
            "result": {"kind": "status", "reply_text": "AQUB ok"},
        }


class MediaDownloader:
    def __init__(self) -> None:
        self.calls = 0

    async def download(self, inbound: AQUBTransportInbound) -> AQUBDownloadedMedia:
        self.calls += 1
        assert inbound.content_kind is AQUBTransportContentKind.PHOTO
        return AQUBDownloadedMedia(
            data=b"\xff\xd8\xfffixture",
            media_type="image/jpeg",
            filename="photo.jpg",
        )


class RecoveringRuntime(Runtime):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def post_envelope(
        self,
        *,
        raw_body: bytes,
        signature: str,
    ) -> dict[str, object]:
        self.attempts += 1
        if self.attempts == 1:
            raise AQUBRuntimeUnavailable("synthetic_runtime_unavailable")
        return await super().post_envelope(raw_body=raw_body, signature=signature)


def binding() -> AQUBTransportBinding:
    return AQUBTransportBinding(
        enabled=True,
        bot_username="@AQubAgentBot",
        group_chat_id=TEST_CHAT_ID,
        ace_user_id="__unbound_ace__",
        alpha_user_id=TEST_ALPHA_ID,
        ready_at=READY_AT,
        receipt_signing_key=SIGNING_KEY,
    )


@pytest.mark.anyio
async def test_general_photo_bypasses_food_queue_and_forwards_bounded_media() -> None:
    store, runtime, downloader = Store(), Runtime(), MediaDownloader()
    general = AQUBTransportBinding(
        enabled=True,
        bot_username="@AQubAgentBot",
        group_chat_id=TEST_CHAT_ID,
        ace_user_id="__unbound_ace__",
        alpha_user_id=TEST_ALPHA_ID,
        ready_at=READY_AT,
        receipt_signing_key=SIGNING_KEY,
        reply_to_source=True,
    )
    result = await AQUBLiveIngressAdapter(
        binding=general,
        store=store,
        runtime=runtime,
        media_downloader=downloader,
    ).handle(photo_inbound())
    assert result.accepted is True
    assert downloader.calls == 1
    envelope = json.loads(runtime.raw_bodies[0])
    assert envelope["content_kind"] == "photo"
    assert envelope["media_input"]["filename"] == "photo.jpg"
    assert envelope["media_input"]["data_base64"] == "/9j/Zml4dHVyZQ=="


def inbound(
    *,
    text: str = "/status",
    update_id: int = 9001,
    actor_id: str = TEST_ALPHA_ID,
    chat_id: str = TEST_CHAT_ID,
    include_program_prompt_reply: bool = True,
) -> AQUBTransportInbound:
    occurred = READY_AT + timedelta(seconds=1)
    message: dict[str, object] = {
        "message_id": 77,
        "date": int(occurred.timestamp()),
        "chat": {"id": int(chat_id), "type": "supergroup"},
        "from": {"id": int(actor_id), "is_bot": False},
        "text": text,
    }
    if include_program_prompt_reply:
        message["reply_to_message"] = {
            "message_id": 66,
            "from": {"id": 999, "is_bot": True},
            "text": AQUB_PROGRAM_PROMPT_TEXT,
        }
    payload = {"update_id": update_id, "message": message}
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id=str(update_id),
        chat_id=chat_id,
        actor_id=actor_id,
        command=text,
        payload=payload,
        occurred_at=occurred,
        received_at=occurred + timedelta(seconds=1),
    )


def photo_inbound(
    *,
    update_id: int = 9006,
    actor_id: str = TEST_ALPHA_ID,
    chat_id: str = TEST_CHAT_ID,
    caption: str | None = "Съел омлет",
    media_group_id: str | None = None,
    message_id: int = 78,
    unique_suffix: str = "",
) -> AQUBTransportInbound:
    occurred = READY_AT + timedelta(seconds=1)
    photo = AQUBPhotoReference(
        file_id="synthetic-file",
        file_unique_id=f"synthetic-unique{unique_suffix}",
        width=1280,
        height=960,
        file_size=12_345,
    )
    message: dict[str, object] = {
        "message_id": message_id,
        "date": int(occurred.timestamp()),
        "chat": {"id": int(chat_id), "type": "supergroup"},
        "from": {"id": int(actor_id), "is_bot": False},
        "photo": [
            {
                "file_id": photo.file_id,
                "file_unique_id": photo.file_unique_id,
                "width": photo.width,
                "height": photo.height,
                "file_size": photo.file_size,
            }
        ],
    }
    if caption is not None:
        message["caption"] = caption
    if media_group_id is not None:
        message["media_group_id"] = media_group_id
    payload = {"update_id": update_id, "message": message}
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id=str(update_id),
        chat_id=chat_id,
        actor_id=actor_id,
        command=None,
        payload=payload,
        occurred_at=occurred,
        received_at=occurred + timedelta(seconds=1),
        content_kind=AQUBTransportContentKind.PHOTO,
        photo=photo,
        media_group_id=media_group_id,
    )


@pytest.mark.anyio
async def test_album_forward_is_one_opaque_ordered_fact_with_all_captions() -> None:
    runtime = Runtime()
    members = [
        photo_inbound(
            update_id=9006,
            caption="Курица",
            media_group_id="raw-provider-group",
            message_id=78,
            unique_suffix="-1",
        ),
        photo_inbound(
            update_id=9007,
            caption="Рис и этикетка",
            media_group_id="raw-provider-group",
            message_id=79,
            unique_suffix="-2",
        ),
        photo_inbound(
            update_id=9008,
            caption="Третий ракурс",
            media_group_id="raw-provider-group",
            message_id=80,
            unique_suffix="-3",
        ),
    ]
    downstream = await forward_aqub_album_result(
        binding=binding(),
        inbounds=members,
        command_receipt_ids=["receipt-1", "receipt-2", "receipt-3"],
        successful_orders=[0, 2],
        linked_text_inbounds=[
            inbound(
                text="Вес 120 г",
                update_id=9009,
                include_program_prompt_reply=False,
            )
        ],
        linked_text_receipt_ids=["receipt-4"],
        media_result={
            "schema_version": "aqub.food-media-analysis.v1",
            "source_kind": "photo",
            "source_sha256": "a" * 64,
            "kind": "meal",
            "confidence": "medium",
            "transcript": None,
            "meal": {
                "label": "курица с рисом",
                "kcal": 510,
                "protein_g": 50,
                "fat_g": 7,
                "carbs_g": 57,
            },
            "receipt": None,
        },
        runtime=runtime,
    )

    assert downstream["ok"] is True
    assert len(runtime.raw_bodies) == 1
    envelope = json.loads(runtime.raw_bodies[0])
    assert envelope["text"] == "Курица\nРис и этикетка\nТретий ракурс"
    assert envelope["linked_texts"] == ["Вес 120 г"]
    assert [item["order"] for item in envelope["media_items"]] == [0, 2]
    assert len(envelope["media_failures"]) == 1
    assert envelope["media_failures"][0]["order"] == 1
    assert envelope["media_failures"][0]["reason_code"] == "download_failed"
    assert envelope["media_failures"][0]["message_ref"].startswith("message:")
    assert len(envelope["source_update_refs"]) == 4
    serialized = runtime.raw_bodies[0].decode()
    assert "raw-provider-group" not in serialized
    assert TEST_CHAT_ID not in serialized

    runtime_without_link = Runtime()
    await forward_aqub_album_result(
        binding=binding(),
        inbounds=members,
        command_receipt_ids=["receipt-1", "receipt-2", "receipt-3"],
        successful_orders=[0, 2],
        media_result=envelope["media_result"],
        runtime=runtime_without_link,
    )
    envelope_without_link = json.loads(runtime_without_link.raw_bodies[0])
    assert "linked_texts" not in envelope_without_link


@pytest.mark.anyio
async def test_pre_ready_event_is_terminal_noop_and_later_event_forwards() -> None:
    store = Store()
    runtime = Runtime()
    adapter = AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    )
    stale = inbound(update_id=8999)
    stale = replace(
        stale,
        occurred_at=READY_AT - timedelta(seconds=1),
        received_at=READY_AT + timedelta(seconds=1),
    )

    rejected = await adapter.handle(stale)

    assert rejected.accepted is False
    assert rejected.duplicate is False
    assert rejected.reason == "aqub_event_before_ready"
    assert store.commands == []
    assert runtime.raw_bodies == []

    accepted = await adapter.handle(inbound(update_id=9000))

    assert accepted.accepted is True
    assert accepted.reason == "aqub_live_committed"
    assert len(store.commands) == 1
    assert len(runtime.raw_bodies) == 1


@pytest.mark.anyio
async def test_invalid_timestamp_order_remains_fail_closed() -> None:
    value = inbound(update_id=8998)
    value = replace(
        value,
        received_at=value.occurred_at - timedelta(seconds=1),
    )

    with pytest.raises(RuntimeError, match="aqub_timestamp_order_invalid"):
        await AQUBLiveIngressAdapter(
            binding=binding(),
            store=Store(),
            runtime=Runtime(),
        ).handle(value)


@pytest.mark.anyio
async def test_live_forward_commits_gate_receipt_then_posts_only_opaque_refs() -> None:
    store = Store()
    runtime = Runtime()
    adapter = AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    )

    result = await adapter.handle(inbound())

    assert result.accepted is True
    assert result.duplicate is False
    assert result.reason == "aqub_live_committed"
    assert len(store.commands) == 1
    assert len(runtime.raw_bodies) == 1

    raw = runtime.raw_bodies[0]
    expected_signature = hmac.new(SIGNING_KEY, raw, hashlib.sha256).hexdigest()
    assert runtime.signatures == [expected_signature]
    envelope = json.loads(raw)
    assert envelope["schema_version"] == "aqub.gate-envelope.v1"
    assert envelope["role"] == "alpha"
    assert envelope["chat_ref"].startswith("chat:")
    assert envelope["actor_ref"].startswith("actor:")
    assert envelope["message_id"].startswith("message:")
    assert envelope["reply_to_message_id"].startswith("message:")
    assert envelope["reply_context"] == "program_prompt"
    serialized = raw.decode()
    forbidden_values = (
        TEST_CHAT_ID,
        TEST_ALPHA_ID,
        '"message_id":77',
        '"message_id":66',
    )
    for forbidden in forbidden_values:
        assert forbidden not in serialized


@pytest.mark.anyio
async def test_exact_live_replay_resumes_runtime_with_the_same_delivery_reference() -> None:
    store = Store()
    runtime = Runtime()
    adapter = AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    )

    first = await adapter.handle(inbound())
    second = await adapter.handle(inbound())

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.reason == "aqub_live_committed"
    assert second.delivery_ref == first.delivery_ref
    assert len(runtime.raw_bodies) == 2


@pytest.mark.anyio
async def test_duplicate_retries_runtime_after_a_committed_durable_only_capture() -> None:
    store = Store()
    runtime = RecoveringRuntime()
    adapter = AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    )

    first = await adapter.handle(inbound(text="Съел омлет"))
    second = await adapter.handle(inbound(text="Съел омлет"))

    assert first.reason == "aqub_live_durable_only"
    assert first.duplicate is False
    assert second.reason == "aqub_live_committed"
    assert second.duplicate is True
    assert second.delivery_ref == first.delivery_ref
    assert runtime.attempts == 2
    assert len(runtime.raw_bodies) == 1


@pytest.mark.anyio
async def test_plain_alpha_text_is_confirmed_after_durable_capture_without_runtime() -> None:
    store = Store()

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=None,
    ).handle(inbound(text="Съел омлет"))

    assert result.accepted is True
    assert result.reason == "aqub_live_durable_only"
    assert result.captured_content is AQUBTransportContentKind.TEXT
    assert result.runtime_error_code == "aqub_runtime_unconfigured"
    assert len(store.commands) == 1
    assert store.commands[0].command == "Съел омлет"


@pytest.mark.anyio
async def test_legacy_alpha_capture_reports_frozen_runtime_without_http() -> None:
    store = Store()

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Frozen runtime must not access HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as http_client:
        result = await AQUBLiveIngressAdapter(
            binding=binding(),
            store=store,
            runtime=HttpAQUBRuntimeClient(
                base_url="https://aqub.example",
                http_client=http_client,
            ),
        ).handle(inbound(text="Выпил воду"))

    assert result.accepted is True
    assert result.reason == "aqub_live_durable_only"
    assert result.captured_content is AQUBTransportContentKind.TEXT
    assert result.runtime_error_code == "aqub_project_frozen"
    assert len(store.commands) == 1


@pytest.mark.anyio
async def test_frozen_client_refuses_before_reading_aqub_response() -> None:
    def binding_missing(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Frozen runtime must not read a provider response")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(binding_missing)
    ) as http_client:
        runtime = HttpAQUBRuntimeClient(
            base_url="https://aqub.example",
            http_client=http_client,
        )
        with pytest.raises(AQUBRuntimeUnavailable) as exc_info:
            await runtime.post_envelope(raw_body=b"{}", signature="synthetic")

    assert exc_info.value.code == "aqub_project_frozen"


@pytest.mark.anyio
async def test_alpha_photo_commits_reference_and_forwards_redacted_envelope() -> None:
    store = Store()
    runtime = Runtime()

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    ).handle(photo_inbound())

    assert result.accepted is True
    assert result.duplicate is False
    assert result.reason == "aqub_live_committed"
    assert result.captured_content is AQUBTransportContentKind.PHOTO
    assert len(store.commands) == 1
    assert store.commands[0].command is None
    assert store.commands[0].photo is not None
    assert len(runtime.raw_bodies) == 1
    envelope = json.loads(runtime.raw_bodies[0])
    assert envelope["content_kind"] == "photo"
    assert envelope["text"] == "Съел омлет"
    assert envelope["media"]["file_ref"].startswith("media:")
    assert "synthetic-file-large" not in runtime.raw_bodies[0].decode()


@pytest.mark.anyio
async def test_exact_photo_replay_reuses_receipt_and_resumes_runtime_idempotently() -> None:
    store = Store()
    runtime = Runtime()
    adapter = AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    )

    first = await adapter.handle(photo_inbound())
    duplicate = await adapter.handle(photo_inbound())

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert len(store.commands) == 2
    assert duplicate.reason == "aqub_live_committed"
    assert duplicate.delivery_ref == first.delivery_ref
    assert len(runtime.raw_bodies) == 2


@pytest.mark.anyio
async def test_non_owner_photo_is_not_stored_or_forwarded() -> None:
    store = Store()
    runtime = Runtime()

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    ).handle(photo_inbound(actor_id=TEST_CANDIDATE_ID))

    assert result.accepted is False
    assert result.reason == "aqub_route_not_owned"
    assert store.commands == []
    assert runtime.raw_bodies == []


@pytest.mark.anyio
async def test_photo_requires_committed_store_acceptance_before_confirmation() -> None:
    store = UncommittedStore()

    with pytest.raises(RuntimeError, match="aqub_store_not_committed"):
        await AQUBLiveIngressAdapter(
            binding=binding(),
            store=store,
            runtime=None,
        ).handle(photo_inbound())

    assert len(store.commands) == 1


@pytest.mark.anyio
async def test_command_keeps_fail_closed_runtime_semantics_after_durable_capture() -> None:
    store = Store()

    with pytest.raises(AQUBRuntimeUnavailable, match="aqub_runtime_unconfigured"):
        await AQUBLiveIngressAdapter(
            binding=binding(),
            store=store,
            runtime=None,
        ).handle(inbound(text="/status"))

    assert len(store.commands) == 1


@pytest.mark.anyio
async def test_candidate_program_gets_no_capture_confirmation_or_runtime_fallback() -> None:
    store = Store()
    value = inbound(
        text="1. Первый шаг\n2. Второй шаг",
        actor_id=TEST_CANDIDATE_ID,
        include_program_prompt_reply=False,
    )

    with pytest.raises(AQUBRuntimeUnavailable, match="aqub_runtime_unconfigured"):
        await AQUBLiveIngressAdapter(
            binding=binding(),
            store=store,
            runtime=None,
        ).handle(value)

    assert len(store.commands) == 1


@pytest.mark.anyio
async def test_unbound_group_actor_is_forwarded_as_opaque_candidate_for_ace_claim() -> None:
    store = Store()
    runtime = Runtime()
    value = inbound(
        text="/ace_join_abcdef123456",
        actor_id=TEST_CANDIDATE_ID,
        include_program_prompt_reply=False,
    )

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    ).handle(value)

    assert result.accepted is True
    assert result.reason == "aqub_live_committed"
    assert len(store.commands) == 1
    assert len(runtime.raw_bodies) == 1
    envelope = json.loads(runtime.raw_bodies[0])
    assert envelope["role"] == "candidate"
    assert envelope["actor_ref"].startswith("actor:")
    assert TEST_CANDIDATE_ID not in runtime.raw_bodies[0].decode()


@pytest.mark.anyio
async def test_claimed_ace_command_family_can_continue_as_candidate_transport() -> None:
    for text in ("/program", "/confirm_program_123456", "/status", "/help"):
        store = Store()
        runtime = Runtime()
        value = inbound(
            text=text,
            actor_id=TEST_CANDIDATE_ID,
            update_id=9002,
            include_program_prompt_reply=False,
        )
        result = await AQUBLiveIngressAdapter(
            binding=binding(), store=store, runtime=runtime
        ).handle(value)
        assert result.accepted is True
        assert json.loads(runtime.raw_bodies[0])["role"] == "candidate"


@pytest.mark.anyio
async def test_program_prompt_reply_from_candidate_is_forwarded() -> None:
    store = Store()
    runtime = Runtime()
    value = inbound(
        text="1. Первый шаг\n2. Второй шаг",
        actor_id=TEST_CANDIDATE_ID,
        update_id=9003,
        include_program_prompt_reply=True,
    )
    result = await AQUBLiveIngressAdapter(binding=binding(), store=store, runtime=runtime).handle(
        value
    )
    assert result.accepted is True
    envelope = json.loads(runtime.raw_bodies[0])
    assert envelope["role"] == "candidate"
    assert envelope["reply_context"] == "program_prompt"


@pytest.mark.anyio
async def test_natural_numbered_program_from_candidate_is_forwarded_without_reply() -> None:
    store = Store()
    runtime = Runtime()
    value = inbound(
        text=(
            "1. Встань, разведи руки, повернись 21 раз.\n"
            "2. Ляг на спину, подними прямые ноги и голову — 21 раз.\n"
            "3. Встань на колени, прогнись назад — 21 раз."
        ),
        actor_id=TEST_CANDIDATE_ID,
        update_id=9005,
        include_program_prompt_reply=False,
    )
    result = await AQUBLiveIngressAdapter(binding=binding(), store=store, runtime=runtime).handle(
        value
    )
    assert result.accepted is True
    envelope = json.loads(runtime.raw_bodies[0])
    assert envelope["role"] == "candidate"
    assert envelope["reply_context"] is None
    assert envelope["text"].startswith("1. Встань")


@pytest.mark.anyio
async def test_ordinary_unbound_group_chatter_is_not_forwarded_or_stored() -> None:
    for text in ("обычное сообщение в группе", "1. Только один пункт"):
        store = Store()
        runtime = Runtime()
        value = inbound(
            text=text,
            actor_id=TEST_CANDIDATE_ID,
            update_id=9004,
            include_program_prompt_reply=False,
        )
        result = await AQUBLiveIngressAdapter(
            binding=binding(), store=store, runtime=runtime
        ).handle(value)
        assert result.accepted is False
        assert result.reason == "aqub_route_not_owned"
        assert store.commands == []
        assert runtime.raw_bodies == []


@pytest.mark.anyio
async def test_actor_outside_pinned_group_is_not_forwarded_or_stored() -> None:
    store = Store()
    runtime = Runtime()
    value = inbound(
        text="/ace_join_abcdef123456",
        actor_id=TEST_CANDIDATE_ID,
        chat_id=TEST_OTHER_CHAT_ID,
        include_program_prompt_reply=False,
    )

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=store,
        runtime=runtime,
    ).handle(value)

    assert result.accepted is False
    assert result.reason == "aqub_route_not_owned"
    assert store.commands == []
    assert runtime.raw_bodies == []


class MediaQueue:
    def __init__(self, *, link_text: bool = False) -> None:
        self.calls: list[tuple[str, AQUBTransportInbound]] = []
        self.link_calls: list[tuple[str, AQUBTransportInbound]] = []
        self.link_times: list[datetime] = []
        self.link_text = link_text

    async def enqueue(
        self,
        *,
        receipt_id: str,
        inbound: AQUBTransportInbound,
        now: datetime,
    ) -> None:
        assert now == inbound.received_at
        self.calls.append((receipt_id, inbound))

    async def link_text_to_pending_album(
        self,
        *,
        receipt_id: str,
        inbound: AQUBTransportInbound,
        now: datetime,
    ) -> bool:
        assert now >= inbound.received_at
        self.link_calls.append((receipt_id, inbound))
        self.link_times.append(now)
        return self.link_text


class DelayedAlbumMediaQueue(MediaQueue):
    async def link_text_to_pending_album(
        self,
        *,
        receipt_id: str,
        inbound: AQUBTransportInbound,
        now: datetime,
    ) -> bool:
        await super().link_text_to_pending_album(
            receipt_id=receipt_id,
            inbound=inbound,
            now=now,
        )
        return len(self.link_calls) == 2


class RecoveringMediaQueue(MediaQueue):
    async def enqueue(
        self,
        *,
        receipt_id: str,
        inbound: AQUBTransportInbound,
        now: datetime,
    ) -> None:
        await super().enqueue(receipt_id=receipt_id, inbound=inbound, now=now)
        if len(self.calls) == 1:
            raise RuntimeError("synthetic_enqueue_failure")


@pytest.mark.anyio
async def test_media_is_queued_silently_until_the_single_final_reply() -> None:
    runtime = Runtime()
    queue = MediaQueue()
    incoming = photo_inbound()
    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=Store(),
        runtime=runtime,
        media_queue=queue,
    ).handle(incoming)

    assert result.accepted is True
    assert result.reason == "aqub_food_media_queued"
    assert result.downstream is None
    assert result.captured_content is None
    assert queue.calls == [("raw-receipt-id", incoming)]
    assert runtime.raw_bodies == []


@pytest.mark.anyio
async def test_duplicate_resumes_media_enqueue_after_first_attempt_failed() -> None:
    runtime = Runtime()
    queue = RecoveringMediaQueue()
    incoming = photo_inbound()
    adapter = AQUBLiveIngressAdapter(
        binding=binding(),
        store=Store(),
        runtime=runtime,
        media_queue=queue,
    )

    with pytest.raises(RuntimeError, match="synthetic_enqueue_failure"):
        await adapter.handle(incoming)
    recovered = await adapter.handle(incoming)

    assert recovered.accepted is True
    assert recovered.duplicate is True
    assert recovered.reason == "aqub_food_media_queued"
    assert len(queue.calls) == 2
    assert queue.calls[0][0] == queue.calls[1][0] == "raw-receipt-id"
    assert runtime.raw_bodies == []


@pytest.mark.anyio
async def test_album_item_is_queued_without_per_photo_reply() -> None:
    runtime = Runtime()
    queue = MediaQueue()
    incoming = photo_inbound(media_group_id="synthetic-group")
    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=Store(),
        runtime=runtime,
        media_queue=queue,
    ).handle(incoming)

    assert result.accepted is True
    assert result.reason == "aqub_food_media_queued"
    assert result.downstream is None
    assert result.captured_content is None
    assert queue.calls == [("raw-receipt-id", incoming)]
    assert runtime.raw_bodies == []


@pytest.mark.anyio
async def test_plain_text_is_buffered_when_one_album_is_pending() -> None:
    runtime = Runtime()
    queue = MediaQueue(link_text=True)
    incoming = inbound(
        text="Вес 120 г",
        update_id=9010,
        include_program_prompt_reply=False,
    )
    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=Store(),
        runtime=runtime,
        media_queue=queue,
    ).handle(incoming)

    assert result.accepted is True
    assert result.reason == "aqub_food_album_text_queued"
    assert result.downstream is None
    assert result.captured_content is None
    assert queue.link_calls == [("raw-receipt-id", incoming)]
    assert runtime.raw_bodies == []


@pytest.mark.anyio
async def test_quantity_only_text_retries_link_after_album_registration_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def no_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.application.aqub_live.asyncio.sleep", no_sleep)
    runtime = Runtime()
    queue = DelayedAlbumMediaQueue()
    incoming = inbound(
        text="Вес 120 г",
        update_id=9011,
        include_program_prompt_reply=False,
    )

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=Store(),
        runtime=runtime,
        media_queue=queue,
    ).handle(incoming)

    assert result.reason == "aqub_food_album_text_queued"
    assert queue.link_calls == [
        ("raw-receipt-id", incoming),
        ("raw-receipt-id", incoming),
    ]
    assert delays == [AQUB_ALBUM_TEXT_GRACE_SECONDS]
    assert queue.link_times == [
        incoming.received_at,
        incoming.received_at + timedelta(seconds=AQUB_ALBUM_TEXT_GRACE_SECONDS),
    ]
    assert runtime.raw_bodies == []


@pytest.mark.anyio
async def test_standalone_quantity_uses_direct_route_after_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def no_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.application.aqub_live.asyncio.sleep", no_sleep)
    runtime = Runtime()
    queue = MediaQueue(link_text=False)
    incoming = inbound(
        text="120 г",
        update_id=9012,
        include_program_prompt_reply=False,
    )

    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=Store(),
        runtime=runtime,
        media_queue=queue,
    ).handle(incoming)

    assert result.reason == "aqub_live_committed"
    assert queue.link_calls == [
        ("raw-receipt-id", incoming),
        ("raw-receipt-id", incoming),
    ]
    assert delays == [AQUB_ALBUM_TEXT_GRACE_SECONDS]
    assert queue.link_times == [
        incoming.received_at,
        incoming.received_at + timedelta(seconds=AQUB_ALBUM_TEXT_GRACE_SECONDS),
    ]
    assert len(runtime.raw_bodies) == 1


@pytest.mark.anyio
async def test_plain_text_preserves_direct_route_without_pending_album() -> None:
    runtime = Runtime()
    queue = MediaQueue(link_text=False)
    incoming = inbound(
        text="Съел яблоко 120 г",
        update_id=9011,
        include_program_prompt_reply=False,
    )
    result = await AQUBLiveIngressAdapter(
        binding=binding(),
        store=Store(),
        runtime=runtime,
        media_queue=queue,
    ).handle(incoming)

    assert result.reason == "aqub_live_committed"
    assert queue.link_calls == [("raw-receipt-id", incoming)]
    assert len(runtime.raw_bodies) == 1


@pytest.mark.anyio
async def test_frozen_http_runtime_does_not_use_the_legacy_provider_budget() -> None:
    captured: dict[str, object] = {}

    class RecordingClient:
        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            captured["url"] = url
            captured.update(kwargs)
            return httpx.Response(200, json={"ok": True})

    runtime = HttpAQUBRuntimeClient(
        base_url="https://aqub.invalid",
        http_client=cast(httpx.AsyncClient, RecordingClient()),
    )
    with pytest.raises(AQUBRuntimeUnavailable, match="aqub_project_frozen"):
        await runtime.post_envelope(
            raw_body=b"{}",
            signature="synthetic-signature",
        )
    assert captured == {}
