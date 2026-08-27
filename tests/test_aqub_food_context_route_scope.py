"""Exact route-marker proofs for the isolated Food context rollout."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.aqub_live import (
    _build_signed_album_envelope_body,
    _build_signed_envelope_body,
)
from app.application.aqub_transport import (
    AQUB_BOT_USERNAME,
    AQUB_FOOD_CONTEXT_ROUTE_SCOPE,
    AQUBPhotoReference,
    AQUBTransportBinding,
    AQUBTransportContentKind,
    AQUBTransportInbound,
    AQUBTransportRole,
)
from app.core.config import Settings
from app.infrastructure.aqub_food_route import load_active_aqub_food_binding
from app.infrastructure.aqub_installation import AQUB_RECEIPT_BOT_KEY
from app.models.aqub import AQUBFoodRoute

NOW = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)
SIGNING_KEY = b"route-scope-synthetic-signing-key-32-bytes"


def _binding(*, route_scope: str | None) -> AQUBTransportBinding:
    return AQUBTransportBinding(
        enabled=True,
        bot_username=AQUB_BOT_USERNAME,
        group_chat_id="synthetic-group",
        ace_user_id="synthetic-ace",
        alpha_user_id="synthetic-alpha",
        ready_at=NOW,
        receipt_signing_key=SIGNING_KEY,
        route_scope=route_scope,
    )


def _inbound(*, photo: bool = False) -> AQUBTransportInbound:
    message: dict[str, object] = {
        "message_id": 101,
        "chat": {"id": -1000000000001, "type": "supergroup"},
        "from": {"id": 1000000001, "is_bot": False},
    }
    reference = None
    if photo:
        reference = AQUBPhotoReference(
            file_id="synthetic-file",
            file_unique_id="synthetic-file-unique",
            width=640,
            height=480,
            file_size=1024,
        )
        message["caption"] = "Я съел яблоко 100 г"
        message["media_group_id"] = "synthetic-album"
    else:
        message["text"] = "Я съел яблоко 100 г"
    return AQUBTransportInbound(
        bot_username=AQUB_BOT_USERNAME,
        external_id="synthetic-update",
        chat_id="synthetic-group",
        actor_id="synthetic-alpha",
        command=None if photo else "Я съел яблоко 100 г",
        payload={"update_id": 100, "message": message},
        occurred_at=NOW,
        received_at=NOW,
        content_kind=(
            AQUBTransportContentKind.PHOTO
            if photo
            else AQUBTransportContentKind.TEXT
        ),
        photo=reference,
        media_group_id="synthetic-album" if photo else None,
    )


def test_route_scope_is_strict_and_signed_for_single_and_album_envelopes() -> None:
    binding = _binding(route_scope=AQUB_FOOD_CONTEXT_ROUTE_SCOPE)
    single = json.loads(
        _build_signed_envelope_body(
            binding=binding,
            inbound=_inbound(),
            role=AQUBTransportRole.ALPHA,
            command_receipt_id="synthetic-receipt",
            payload_sha256="a" * 64,
        )
    )
    assert single["route_scope"] == AQUB_FOOD_CONTEXT_ROUTE_SCOPE

    photo = _inbound(photo=True)
    album = json.loads(
        _build_signed_album_envelope_body(
            binding=binding,
            inbounds=[photo],
            command_receipt_ids=["synthetic-receipt"],
            successful_orders=[0],
            media_result={
                "schema_version": "aqub.food-media-evidence.v1",
                "source_kind": "photo",
                "source_sha256": "b" * 64,
                "kind": "photo",
                "confidence": "high",
                "transcript": None,
                "ocr_text": "ЯБЛОКО 100 г",
                "observations": [],
                "receipt": None,
            },
            linked_text_inbounds=[],
            linked_text_receipt_ids=[],
        )
    )
    assert album["route_scope"] == AQUB_FOOD_CONTEXT_ROUTE_SCOPE

    with pytest.raises(ValueError, match="aqub_route_scope_invalid"):
        _binding(route_scope="all_food")


@pytest.mark.anyio
async def test_only_active_exact_food_route_receives_context_marker() -> None:
    route = AQUBFoodRoute(
        id=uuid4(),
        bot_key=AQUB_RECEIPT_BOT_KEY,
        group_chat_id="synthetic-group",
        actor_user_id="synthetic-alpha",
        evidence_receipt_id=uuid4(),
        status="active",
        bound_at=NOW,
        activated_at=NOW,
    )

    class Session:
        async def scalar(self, _query):
            return route

    settings = Settings(
        _env_file=None,
        GATE_AQUB_RECEIPT_SIGNING_KEY=SIGNING_KEY.decode(),
    )
    selected = await load_active_aqub_food_binding(
        Session(),
        settings,
        chat_id="synthetic-group",
    )
    assert selected is not None
    assert selected.route_scope == AQUB_FOOD_CONTEXT_ROUTE_SCOPE
    assert _binding(route_scope=None).route_scope is None
