"""Owner-only AQUB Food media processing contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.application.aqub_transport import (
    AQUBTransportContentKind,
    AQUBTransportInbound,
)

JsonObject = dict[str, Any]
MAX_AQUB_FOOD_MEDIA_BYTES = 20_000_000
MAX_AQUB_FOOD_TRANSCRIPT_CHARS = 20_000


class AQUBFoodMediaError(RuntimeError):
    """Safe error code for the bounded Food media path."""


class AQUBFoodMediaClaimDisposition(StrEnum):
    PROCESS = "process"
    REPLAY = "replay"
    IN_PROGRESS = "in_progress"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True, repr=False)
class AQUBFoodMediaClaim:
    disposition: AQUBFoodMediaClaimDisposition
    claim_token: str | None = field(default=None, repr=False)
    raw_data: bytes | None = field(default=None, repr=False)
    media_type: str | None = None
    filename: str | None = None
    result_json: Mapping[str, Any] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class AQUBDownloadedMedia:
    data: bytes = field(repr=False)
    media_type: str
    filename: str

    def __post_init__(self) -> None:
        if not self.data or len(self.data) > MAX_AQUB_FOOD_MEDIA_BYTES:
            raise AQUBFoodMediaError("aqub_food_media_size_invalid")
        if not self.media_type or len(self.media_type) > 128:
            raise AQUBFoodMediaError("aqub_food_media_type_invalid")
        if not self.filename or len(self.filename) > 255:
            raise AQUBFoodMediaError("aqub_food_media_filename_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class AQUBFoodMediaAnalysis:
    result_json: Mapping[str, Any] = field(repr=False)
    provider_model: str
    provider_request_ref: str | None = field(default=None, repr=False)


class AQUBFoodMediaStore(Protocol):
    async def claim(
        self,
        *,
        receipt_id: str,
        content_kind: AQUBTransportContentKind,
        now: datetime,
    ) -> AQUBFoodMediaClaim: ...

    async def save_raw(
        self,
        *,
        receipt_id: str,
        claim_token: str,
        media: AQUBDownloadedMedia,
        source_sha256: str,
        now: datetime,
    ) -> None: ...

    async def complete(
        self,
        *,
        receipt_id: str,
        claim_token: str,
        analysis: AQUBFoodMediaAnalysis,
        now: datetime,
    ) -> None: ...

    async def fail(
        self,
        *,
        receipt_id: str,
        claim_token: str,
        error_code: str,
        now: datetime,
    ) -> None: ...


class AQUBFoodMediaDownloader(Protocol):
    async def download(self, inbound: AQUBTransportInbound) -> AQUBDownloadedMedia: ...


class AQUBFoodMediaAnalyzer(Protocol):
    async def analyze(
        self,
        *,
        content_kind: AQUBTransportContentKind,
        media: AQUBDownloadedMedia,
        caption: str,
        source_sha256: str,
    ) -> AQUBFoodMediaAnalysis: ...


class ProcessAQUBFoodMedia:
    """Download once, durably retain the original, then analyze once per claim."""

    def __init__(
        self,
        *,
        store: AQUBFoodMediaStore,
        downloader: AQUBFoodMediaDownloader,
        analyzer: AQUBFoodMediaAnalyzer,
    ) -> None:
        self._store = store
        self._downloader = downloader
        self._analyzer = analyzer

    async def process(
        self,
        *,
        receipt_id: str,
        inbound: AQUBTransportInbound,
    ) -> JsonObject:
        if inbound.content_kind not in {
            AQUBTransportContentKind.PHOTO,
            AQUBTransportContentKind.VOICE,
        }:
            raise AQUBFoodMediaError("aqub_food_media_not_applicable")
        claim = await self._store.claim(
            receipt_id=receipt_id,
            content_kind=inbound.content_kind,
            now=inbound.received_at,
        )
        if claim.disposition is AQUBFoodMediaClaimDisposition.REPLAY:
            if claim.result_json is None:
                raise AQUBFoodMediaError("aqub_food_media_replay_missing")
            return dict(claim.result_json)
        if claim.disposition is AQUBFoodMediaClaimDisposition.IN_PROGRESS:
            raise AQUBFoodMediaError("aqub_food_media_in_progress")
        if claim.disposition is AQUBFoodMediaClaimDisposition.EXHAUSTED:
            raise AQUBFoodMediaError("aqub_food_media_attempts_exhausted")
        if claim.claim_token is None:
            raise AQUBFoodMediaError("aqub_food_media_claim_invalid")

        try:
            if claim.raw_data is None:
                media = await self._downloader.download(inbound)
                source_sha256 = hashlib.sha256(media.data).hexdigest()
                await self._store.save_raw(
                    receipt_id=receipt_id,
                    claim_token=claim.claim_token,
                    media=media,
                    source_sha256=source_sha256,
                    now=inbound.received_at,
                )
            else:
                if claim.media_type is None or claim.filename is None:
                    raise AQUBFoodMediaError("aqub_food_media_raw_metadata_missing")
                media = AQUBDownloadedMedia(
                    data=claim.raw_data,
                    media_type=claim.media_type,
                    filename=claim.filename,
                )
                source_sha256 = hashlib.sha256(media.data).hexdigest()
            analysis = await self._analyzer.analyze(
                content_kind=inbound.content_kind,
                media=media,
                caption=_caption(inbound),
                source_sha256=source_sha256,
            )
            normalized = validate_food_media_result(
                analysis.result_json,
                source_kind=inbound.content_kind.value,
                source_sha256=source_sha256,
            )
            durable = AQUBFoodMediaAnalysis(
                result_json=normalized,
                provider_model=analysis.provider_model,
                provider_request_ref=analysis.provider_request_ref,
            )
            await self._store.complete(
                receipt_id=receipt_id,
                claim_token=claim.claim_token,
                analysis=durable,
                now=inbound.received_at,
            )
            return normalized
        except Exception as exc:
            error_code = _safe_error_code(exc)
            await self._store.fail(
                receipt_id=receipt_id,
                claim_token=claim.claim_token,
                error_code=error_code,
                now=inbound.received_at,
            )
            if isinstance(exc, AQUBFoodMediaError):
                raise
            raise AQUBFoodMediaError(error_code) from exc


def validate_food_media_result(
    value: Mapping[str, Any],
    *,
    source_kind: str,
    source_sha256: str,
) -> JsonObject:
    """Validate the provider-independent Food result before persistence/forwarding."""

    kind = value.get("kind")
    confidence = value.get("confidence")
    transcript = value.get("transcript")
    meal = value.get("meal")
    receipt = value.get("receipt")
    if kind not in {"meal", "receipt", "unknown"}:
        raise AQUBFoodMediaError("aqub_food_media_result_kind_invalid")
    if confidence not in {"high", "medium", "low"}:
        raise AQUBFoodMediaError("aqub_food_media_confidence_invalid")
    if transcript is not None and (
        not isinstance(transcript, str)
        or not transcript.strip()
        or len(transcript) > MAX_AQUB_FOOD_TRANSCRIPT_CHARS
    ):
        raise AQUBFoodMediaError("aqub_food_media_transcript_invalid")
    if kind == "meal":
        normalized_meal = _meal(meal)
        normalized_receipt = None
    elif kind == "receipt":
        normalized_meal = None
        normalized_receipt = _receipt(receipt)
    else:
        if meal is not None or receipt is not None:
            raise AQUBFoodMediaError("aqub_food_media_unknown_payload_invalid")
        normalized_meal = None
        normalized_receipt = None
    return {
        "schema_version": "aqub.food-media-analysis.v1",
        "source_kind": source_kind,
        "source_sha256": source_sha256,
        "kind": kind,
        "confidence": confidence,
        "transcript": transcript.strip() if isinstance(transcript, str) else None,
        "meal": normalized_meal,
        "receipt": normalized_receipt,
    }


def _meal(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        raise AQUBFoodMediaError("aqub_food_media_meal_invalid")
    label = value.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 300:
        raise AQUBFoodMediaError("aqub_food_media_meal_label_invalid")
    result: JsonObject = {"label": label.strip()}
    for key in ("kcal", "protein_g", "fat_g", "carbs_g"):
        amount = value.get(key)
        if type(amount) is not int or not 0 <= amount <= 100_000:
            raise AQUBFoodMediaError("aqub_food_media_meal_amount_invalid")
        result[key] = amount
    return result


def _receipt(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        raise AQUBFoodMediaError("aqub_food_media_receipt_invalid")
    result: JsonObject = {}
    for key in ("merchant", "purchased_at", "currency", "total_text"):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or len(item) > 300):
            raise AQUBFoodMediaError("aqub_food_media_receipt_field_invalid")
        result[key] = item.strip() if isinstance(item, str) and item.strip() else None
    items = value.get("items")
    if not isinstance(items, list) or len(items) > 100:
        raise AQUBFoodMediaError("aqub_food_media_receipt_items_invalid")
    normalized_items: list[JsonObject] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise AQUBFoodMediaError("aqub_food_media_receipt_item_invalid")
        normalized: JsonObject = {}
        for key in ("name", "quantity_text", "unit_price_text", "line_total_text"):
            text = item.get(key)
            if text is not None and (not isinstance(text, str) or len(text) > 300):
                raise AQUBFoodMediaError("aqub_food_media_receipt_item_field_invalid")
            normalized[key] = text.strip() if isinstance(text, str) and text.strip() else None
        if normalized["name"] is None:
            raise AQUBFoodMediaError("aqub_food_media_receipt_item_name_missing")
        normalized_items.append(normalized)
    result["items"] = normalized_items
    return result


def _caption(inbound: AQUBTransportInbound) -> str:
    message = inbound.payload.get("message")
    if isinstance(message, Mapping) and isinstance(message.get("caption"), str):
        return str(message["caption"]).strip()[:1_024]
    return ""


def _safe_error_code(error: Exception) -> str:
    value = str(error)
    if (
        value
        and len(value) <= 128
        and all(character.isalnum() or character in "_.-" for character in value)
    ):
        return value
    return "aqub_food_media_processing_failed"
