from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from app.application.aqub_food_media import (
    AQUBDownloadedMedia,
    AQUBFoodMediaAnalysis,
    AQUBFoodMediaClaim,
    AQUBFoodMediaClaimDisposition,
    AQUBFoodMediaError,
    ProcessAQUBFoodMedia,
    combine_album_media_results,
    validate_food_media_result,
)
from app.application.aqub_transport import (
    AQUBDocumentReference,
    AQUBPhotoReference,
    AQUBTransportContentKind,
    AQUBTransportInbound,
    AQUBVoiceReference,
)
from app.infrastructure.aqub_food_media import (
    _ANALYSIS_SCHEMA,
    OpenAIAQUBFoodMediaAnalyzer,
    TelegramAQUBFoodMediaDownloader,
    _validate_magic,
)
from app.infrastructure.telegram_voice import TelegramVoiceNormalizer


def _inbound(kind: AQUBTransportContentKind) -> AQUBTransportInbound:
    photo = None
    voice = None
    message: dict[str, Any] = {
        "message_id": 7,
        "from": {"id": 1, "is_bot": False},
        "date": 1_787_300_000,
        "chat": {"id": -10, "type": "supergroup"},
    }
    if kind is AQUBTransportContentKind.PHOTO:
        photo = AQUBPhotoReference(
            file_id="photo-provider-id",
            file_unique_id="photo-stable-id",
            width=640,
            height=480,
            file_size=7,
        )
        message["photo"] = [
            {
                "file_id": photo.file_id,
                "file_unique_id": photo.file_unique_id,
                "width": photo.width,
                "height": photo.height,
                "file_size": photo.file_size,
            }
        ]
        message["caption"] = "Обед"
    else:
        voice = AQUBVoiceReference(
            file_id="voice-provider-id",
            file_unique_id="voice-stable-id",
            duration=4,
            mime_type="audio/ogg",
            file_size=8,
        )
        message["voice"] = {
            "file_id": voice.file_id,
            "file_unique_id": voice.file_unique_id,
            "duration": voice.duration,
            "mime_type": voice.mime_type,
            "file_size": voice.file_size,
        }
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id="100",
        chat_id="-10",
        actor_id="1",
        command=None,
        payload={"update_id": 100, "message": message},
        occurred_at=datetime(2026, 8, 21, 12, tzinfo=UTC),
        received_at=datetime(2026, 8, 21, 12, 0, 1, tzinfo=UTC),
        content_kind=kind,
        photo=photo,
        voice=voice,
    )



def _document_inbound(
    *, filename: str, mime_type: str, file_size: int
) -> AQUBTransportInbound:
    document = AQUBDocumentReference(
        file_id="document-provider-id",
        file_unique_id="document-stable-id",
        file_name=filename,
        mime_type=mime_type,
        file_size=file_size,
    )
    message: dict[str, Any] = {
        "message_id": 8,
        "from": {"id": 1, "is_bot": False},
        "date": 1_787_300_000,
        "chat": {"id": -10, "type": "supergroup"},
        "document": {
            "file_id": document.file_id,
            "file_unique_id": document.file_unique_id,
            "file_name": document.file_name,
            "mime_type": document.mime_type,
            "file_size": document.file_size,
        },
    }
    return AQUBTransportInbound(
        bot_username="@AQubAgentBot",
        external_id="101",
        chat_id="-10",
        actor_id="1",
        command=None,
        payload={"update_id": 101, "message": message},
        occurred_at=datetime(2026, 8, 21, 12, tzinfo=UTC),
        received_at=datetime(2026, 8, 21, 12, 0, 1, tzinfo=UTC),
        content_kind=AQUBTransportContentKind.DOCUMENT,
        document=document,
    )

class _Store:
    def __init__(self) -> None:
        self.result: dict[str, Any] | None = None
        self.raw_saved = 0
        self.failed: list[str] = []

    async def claim(self, **_kwargs: Any) -> AQUBFoodMediaClaim:
        if self.result is not None:
            return AQUBFoodMediaClaim(
                AQUBFoodMediaClaimDisposition.REPLAY,
                result_json=self.result,
            )
        return AQUBFoodMediaClaim(
            AQUBFoodMediaClaimDisposition.PROCESS,
            claim_token="11111111-1111-4111-8111-111111111111",
        )

    async def save_raw(self, **_kwargs: Any) -> None:
        self.raw_saved += 1

    async def complete(self, *, analysis: AQUBFoodMediaAnalysis, **_kwargs: Any) -> None:
        self.result = dict(analysis.result_json)

    async def fail(self, *, error_code: str, **_kwargs: Any) -> None:
        self.failed.append(error_code)


class _Downloader:
    def __init__(self) -> None:
        self.calls = 0

    async def download(self, _inbound: AQUBTransportInbound) -> AQUBDownloadedMedia:
        self.calls += 1
        return AQUBDownloadedMedia(
            data=b"\xff\xd8\xffmeal",
            media_type="image/jpeg",
            filename="meal.jpg",
        )


class _Analyzer:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, **_kwargs: Any) -> AQUBFoodMediaAnalysis:
        self.calls += 1
        return AQUBFoodMediaAnalysis(
            result_json={
                "kind": "photo",
                "confidence": "medium",
                "transcript": None,
                "ocr_text": None,
                "observations": [
                    {
                        "label": "курица с рисом",
                        "observation_type": "food",
                        "quantity_text": None,
                        "visible_text": None,
                        "confidence": "medium",
                        "source_indices": None,
                    }
                ],
                "receipt": None,
            },
            provider_model="fixture",
        )


class _UnexpectedFailureAnalyzer:
    async def analyze(self, **_kwargs: Any) -> AQUBFoodMediaAnalysis:
        raise OSError("provider socket details must not escape")


@pytest.mark.anyio
async def test_processing_saves_raw_before_analysis_and_replays_without_provider() -> None:
    store = _Store()
    downloader = _Downloader()
    analyzer = _Analyzer()
    service = ProcessAQUBFoodMedia(
        store=store,
        downloader=downloader,
        analyzer=analyzer,
    )
    first = await service.process(
        receipt_id="22222222-2222-4222-8222-222222222222",
        inbound=_inbound(AQUBTransportContentKind.PHOTO),
    )
    replay = await service.process(
        receipt_id="22222222-2222-4222-8222-222222222222",
        inbound=_inbound(AQUBTransportContentKind.PHOTO),
    )
    assert first == replay
    assert first["source_kind"] == "photo"
    assert len(first["source_sha256"]) == 64
    assert store.raw_saved == 1
    assert downloader.calls == 1
    assert analyzer.calls == 1


@pytest.mark.anyio
async def test_processing_persists_and_surfaces_only_safe_failure_code() -> None:
    store = _Store()
    service = ProcessAQUBFoodMedia(
        store=store,
        downloader=_Downloader(),
        analyzer=_UnexpectedFailureAnalyzer(),
    )

    with pytest.raises(AQUBFoodMediaError, match="aqub_food_media_processing_failed"):
        await service.process(
            receipt_id="22222222-2222-4222-8222-222222222222",
            inbound=_inbound(AQUBTransportContentKind.PHOTO),
        )

    assert store.raw_saved == 1
    assert store.failed == ["aqub_food_media_processing_failed"]


def test_receipt_result_keeps_purchase_separate_from_consumption() -> None:
    result = validate_food_media_result(
        {
            "kind": "receipt",
            "confidence": "high",
            "transcript": None,
            "ocr_text": "Магазин\nТворог 4.50",
            "observations": [],
            "receipt": {
                "merchant": "Магазин",
                "purchased_at": "2026-08-21",
                "currency": "BYN",
                "total_text": "4.50",
                "items": [
                    {
                        "name": "Творог",
                        "quantity_text": "1",
                        "unit_price_text": "4.50",
                        "line_total_text": "4.50",
                    }
                ],
            },
        },
        source_kind="photo",
        source_sha256="a" * 64,
    )
    assert result["kind"] == "receipt"
    assert "meal" not in result
    assert result["receipt"]["items"][0]["name"] == "Творог"


def test_album_combiner_deduplicates_angles_and_keeps_distinct_products() -> None:
    values = [
        validate_food_media_result(
            {
                "kind": "photo",
                "confidence": "medium",
                "transcript": None,
                "ocr_text": None,
                "observations": [
                    {
                        "label": label,
                        "observation_type": "food",
                        "quantity_text": quantity,
                        "visible_text": None,
                        "confidence": "medium",
                        "source_indices": None,
                    }
                ],
                "receipt": None,
            },
            source_kind="photo",
            source_sha256=source,
        )
        for label, quantity, source in [
            ("курица", None, "a" * 64),
            ("Курица", None, "b" * 64),
            ("рис", "200 г", "c" * 64),
        ]
    ]

    combined = combine_album_media_results(values)

    assert combined["kind"] == "photo"
    assert [item["label"] for item in combined["observations"]] == [
        "курица",
        "рис",
    ]
    assert combined["observations"][0]["source_indices"] == [0]
    assert all("kcal" not in item for item in combined["observations"])
    assert len(combined["source_sha256"]) == 64


def test_photo_evidence_preserves_visible_quantity_without_nutrition() -> None:
    result = validate_food_media_result(
        {
            "kind": "photo",
            "confidence": "medium",
            "transcript": None,
            "ocr_text": "400 г",
            "observations": [
                {
                    "label": "зернёный творог",
                    "observation_type": "food",
                    "quantity_text": "400 г",
                    "visible_text": "400 г",
                    "confidence": "high",
                    "source_indices": None,
                }
            ],
            "receipt": None,
        },
        source_kind="photo",
        source_sha256="b" * 64,
    )
    assert result["observations"][0]["quantity_text"] == "400 г"
    assert "kcal" not in json.dumps(result)


@pytest.mark.anyio
async def test_telegram_downloader_uses_one_metadata_and_one_bounded_file_call() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"file_path": "photos/meal.jpg", "file_size": 7},
                },
            )
        return httpx.Response(
            200,
            content=b"\xff\xd8\xffmeal",
            headers={"content-type": "application/octet-stream", "content-length": "7"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        media = await TelegramAQUBFoodMediaDownloader(
            bot_token="test-token",
            client=client,
        ).download(_inbound(AQUBTransportContentKind.PHOTO))
    assert media.media_type == "image/jpeg"
    assert media.data == b"\xff\xd8\xffmeal"
    assert len(calls) == 2



@pytest.mark.parametrize(
    ("filename", "declared_type", "data"),
    [
        ("canary.txt", "text/plain", b"hello"),
        (
            "canary.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK\x03\x04fixture",
        ),
    ],
)
@pytest.mark.anyio
async def test_telegram_document_downloader_uses_declared_type_when_transport_is_generic(
    filename: str,
    declared_type: str,
    data: bytes,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "file_path": f"documents/{filename}",
                        "file_size": len(data),
                    },
                },
            )
        return httpx.Response(
            200,
            content=data,
            headers={
                "content-type": "application/octet-stream",
                "content-length": str(len(data)),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        media = await TelegramAQUBFoodMediaDownloader(
            bot_token="test-token",
            client=client,
        ).download(
            _document_inbound(
                filename=filename,
                mime_type=declared_type,
                file_size=len(data),
            )
        )

    assert media.media_type == declared_type
    assert media.filename == filename
    assert media.data == data

def test_image_magic_rejects_non_binary_non_image_content_type() -> None:
    with pytest.raises(AQUBFoodMediaError, match="aqub_food_media_image_invalid"):
        _validate_magic(
            b"\xff\xd8\xffmeal",
            content_kind=AQUBTransportContentKind.PHOTO,
            media_type="text/html",
        )


def test_openai_schema_omits_unsupported_string_length_keywords() -> None:
    def assert_supported(value: Any) -> None:
        if isinstance(value, dict):
            assert "minLength" not in value
            assert "maxLength" not in value
            for child in value.values():
                assert_supported(child)
        elif isinstance(value, list):
            for child in value:
                assert_supported(child)

    assert_supported(_ANALYSIS_SCHEMA)


@pytest.mark.anyio
async def test_openai_photo_request_is_evidence_only_and_has_no_tools() -> None:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        serialized = json.dumps(body, ensure_ascii=False)
        assert body["store"] is False
        assert body["tools"] == []
        assert body["tool_choice"] == "none"
        assert body["parallel_tool_calls"] is False
        assert "kcal" not in serialized
        assert "protein_g" not in serialized
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": json.dumps(
                    {
                        "kind": "photo",
                        "confidence": "high",
                        "transcript": None,
                        "ocr_text": "400 г",
                        "observations": [
                            {
                                "label": "зернёный творог",
                                "observation_type": "food",
                                "quantity_text": "400 г",
                                "visible_text": "400 г",
                                "confidence": "high",
                                "source_indices": None,
                            }
                        ],
                        "receipt": None,
                    },
                    ensure_ascii=False,
                ),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIAQUBFoodMediaAnalyzer(
            api_key="server-secret",
            api_base_url="https://api.openai.com/v1",
            model="gpt-5.6-luna",
            transcription_model="gpt-4o-mini-transcribe",
            client=client,
            data_gate_approved=True,
        ).analyze(
            content_kind=AQUBTransportContentKind.PHOTO,
            media=AQUBDownloadedMedia(
                data=b"\xff\xd8\xffmeal",
                media_type="image/jpeg",
                filename="meal.jpg",
            ),
            caption="мой обед",
            source_sha256="b" * 64,
        )

    assert len(requests) == 1
    assert result.result_json["observations"][0]["quantity_text"] == "400 г"


def test_legacy_meal_nutrition_shape_is_rejected() -> None:
    with pytest.raises(AQUBFoodMediaError, match="result_kind_invalid"):
        validate_food_media_result(
            {
                "kind": "meal",
                "confidence": "high",
                "transcript": None,
                "ocr_text": None,
                "observations": [],
                "meal": {"label": "омлет", "kcal": 350},
                "receipt": None,
            },
            source_kind="photo",
            source_sha256="c" * 64,
        )


@pytest.mark.anyio
async def test_openai_voice_path_transcribes_once_and_defers_food_decisions() -> None:
    requests: list[str] = []

    async def normalize_ogg(data: bytes) -> tuple[int, bytes, bytes]:
        assert data == b"OggSvoice"
        return 0, b"RIFF-normalized-WAVE", b""

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/audio/transcriptions"):
            assert b'filename="telegram-voice.wav"' in request.content
            assert b"Content-Type: audio/wav" in request.content
            assert b"RIFF-normalized-WAVE" in request.content
            return httpx.Response(
                200,
                json={"text": "Съел курицу с рисом"},
                headers={"x-request-id": "transcription-1"},
            )
        raise AssertionError("voice evidence must not trigger a second model call")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIAQUBFoodMediaAnalyzer(
            api_key="server-secret",
            api_base_url="https://api.openai.com/v1",
            model="gpt-5.6-luna",
            transcription_model="gpt-4o-mini-transcribe",
            client=client,
            data_gate_approved=True,
            voice_normalizer=TelegramVoiceNormalizer(runner=normalize_ogg),
        ).analyze(
            content_kind=AQUBTransportContentKind.VOICE,
            media=AQUBDownloadedMedia(
                data=b"OggSvoice",
                media_type="audio/ogg",
                filename="voice.ogg",
            ),
            caption="",
            source_sha256="b" * 64,
        )
    assert result.result_json["transcript"] == "Съел курицу с рисом"
    assert result.result_json["kind"] == "voice"
    assert result.result_json["observations"] == []
    assert result.provider_model == "gpt-4o-mini-transcribe"
    assert result.provider_request_ref == "transcription-1"
    assert requests == ["/v1/audio/transcriptions"]


@pytest.mark.anyio
async def test_openai_voice_path_fails_closed_when_ogg_normalization_fails() -> None:
    async def fail_normalization(_data: bytes) -> tuple[int, bytes, bytes]:
        return 1, b"", b"safe diagnostic"

    async def unexpected_provider_call(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called after normalization failure")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_provider_call)) as client:
        analyzer = OpenAIAQUBFoodMediaAnalyzer(
            api_key="server-secret",
            api_base_url="https://api.openai.com/v1",
            model="gpt-5.6-luna",
            transcription_model="gpt-4o-mini-transcribe",
            client=client,
            data_gate_approved=True,
            voice_normalizer=TelegramVoiceNormalizer(runner=fail_normalization),
        )
        with pytest.raises(
            AQUBFoodMediaError,
            match="aqub_food_media_voice_normalization_failed",
        ):
            await analyzer.analyze(
                content_kind=AQUBTransportContentKind.VOICE,
                media=AQUBDownloadedMedia(
                    data=b"OggSvoice",
                    media_type="audio/ogg",
                    filename="voice.ogg",
                ),
                caption="",
                source_sha256="b" * 64,
            )


@pytest.mark.anyio
async def test_analyzer_is_not_constructed_without_explicit_data_gate() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(AQUBFoodMediaError, match="data_gate_not_approved"):
            OpenAIAQUBFoodMediaAnalyzer(
                api_key="server-secret",
                api_base_url="https://api.openai.com/v1",
                model="gpt-5.6-luna",
                transcription_model="gpt-4o-mini-transcribe",
                client=client,
                data_gate_approved=False,
            )
