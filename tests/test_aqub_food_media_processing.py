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
    validate_food_media_result,
)
from app.application.aqub_transport import (
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
                "kind": "meal",
                "confidence": "medium",
                "transcript": None,
                "meal": {
                    "label": "курица с рисом",
                    "kcal": 620,
                    "protein_g": 45,
                    "fat_g": 18,
                    "carbs_g": 70,
                },
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


def test_receipt_result_keeps_purchase_separate_from_meal() -> None:
    result = validate_food_media_result(
        {
            "kind": "receipt",
            "confidence": "high",
            "transcript": "Магазин\nТворог 4.50",
            "meal": None,
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
    assert result["meal"] is None
    assert result["receipt"]["items"][0]["name"] == "Творог"


def test_meal_result_keeps_itemized_food_and_recomputes_exact_totals() -> None:
    result = validate_food_media_result(
        {
            "kind": "meal",
            "confidence": "medium",
            "transcript": "Съел овсяные хлопья с молоком",
            "meal": {
                "label": "овсяные хлопья с молоком",
                "kcal": 999,
                "protein_g": 999,
                "fat_g": 999,
                "carbs_g": 999,
                "items": [
                    {
                        "label": "овсяные хлопья 100 г",
                        "kcal": 370,
                        "protein_g": 13,
                        "fat_g": 7,
                        "carbs_g": 62,
                    },
                    {
                        "label": "молоко 400 мл",
                        "kcal": 240,
                        "protein_g": 12,
                        "fat_g": 12,
                        "carbs_g": 20,
                    },
                ],
            },
            "receipt": None,
        },
        source_kind="voice",
        source_sha256="b" * 64,
    )
    assert [item["label"] for item in result["meal"]["items"]] == [
        "овсяные хлопья 100 г",
        "молоко 400 мл",
    ]
    assert result["meal"]["kcal"] == 610
    assert result["meal"]["protein_g"] == 25
    assert result["meal"]["fat_g"] == 19
    assert result["meal"]["carbs_g"] == 82


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
async def test_openai_voice_path_transcribes_then_returns_structured_meal() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(
                200,
                json={"text": "Съел курицу с рисом"},
                headers={"x-request-id": "transcription-1"},
            )
        body = json.loads(request.content)
        assert body["reasoning"] == {"effort": "none"}
        assert body["store"] is False
        assert body["tools"] == []
        assert "список отдельно съеденных продуктов" in body["instructions"]
        meal_schema = body["text"]["format"]["schema"]["properties"]["meal"]
        assert "items" in meal_schema["anyOf"][1]["required"]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": json.dumps(
                    {
                        "kind": "meal",
                        "confidence": "medium",
                        "transcript": None,
                        "meal": {
                            "label": "курица с рисом",
                            "kcal": 620,
                            "protein_g": 45,
                            "fat_g": 18,
                            "carbs_g": 70,
                            "items": [
                                {
                                    "label": "курица",
                                    "kcal": 300,
                                    "protein_g": 40,
                                    "fat_g": 10,
                                    "carbs_g": 0,
                                },
                                {
                                    "label": "рис",
                                    "kcal": 320,
                                    "protein_g": 5,
                                    "fat_g": 8,
                                    "carbs_g": 70,
                                },
                            ],
                        },
                        "receipt": None,
                    },
                    ensure_ascii=False,
                ),
            },
            headers={"x-request-id": "analysis-1"},
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
    assert result.provider_request_ref == "transcription-1;analysis-1"
    assert requests == ["/v1/audio/transcriptions", "/v1/responses"]


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
