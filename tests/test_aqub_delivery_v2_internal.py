"""HTTP boundary proofs for the default-off signed AQUB v2 delivery route."""

from __future__ import annotations

import hashlib
import hmac
import json
from uuid import uuid4

from app.api.aqub_delivery_v2_internal import (
    get_aqub_v2_delivery_store,
    router,
)
from app.application.aqub_contract_v2 import AQUB_V2_MAX_JSON_BYTES
from app.core.config import Settings, get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "0123456789abcdef0123456789abcdef"


def command() -> dict[str, object]:
    return {
        "schema_version": "aqub.delivery-command.v2",
        "command_id": str(uuid4()),
        "idempotency_key": "food/reply/http-boundary",
        "installation_ref": "installation:aopaque0",
        "cube_ref": "cube:aopaque0",
        "route_generation": 7,
        "conversation_ref": "conversation:aopaque0",
        "requested_at": "2026-08-19T12:00:00.000Z",
        "payload": {
            "delivery_type": "food.fact_reply",
            "kind": "text",
            "media_type": "text/plain",
            "text": "Synthetic only",
            "reply_to_message_ref": None,
        },
    }


class Store:
    def __init__(self, *, rejected: bool = False) -> None:
        self.rejected = rejected
        self.documents: list[dict[str, object]] = []

    async def accept(self, document) -> dict[str, object]:
        value = document.to_wire()
        self.documents.append(value)
        status = "rejected" if self.rejected else "committed"
        ack: dict[str, object] = {
            "schema_version": "aqub.delivery-ack.v2",
            "ack_ref": "ack:aopaque0",
            "command_id": value["command_id"],
            "idempotency_key": value["idempotency_key"],
            "installation_ref": value["installation_ref"],
            "cube_ref": value["cube_ref"],
            "route_generation": value["route_generation"],
            "observed_at": "2026-08-19T12:00:01.000Z",
            "status": status,
        }
        if self.rejected:
            ack["reason_code"] = "AQUB_V2_ROUTE_NOT_ACTIVE"
        return {"ack": ack, "receipt": None}


def settings(*, enabled: bool = True, secret: str = SECRET) -> Settings:
    return Settings(
        _env_file=None,
        GATE_AQUB_V2_DELIVERY_ENDPOINT_ENABLED=enabled,
        GATE_AQUB_V2_DELIVERY_HMAC_SECRET=secret,
    )


def client(config: Settings, store: Store) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: config
    app.dependency_overrides[get_aqub_v2_delivery_store] = lambda: store
    return TestClient(app)


def raw_command() -> bytes:
    return json.dumps(
        command(),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()


def signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def post(http: TestClient, body: bytes, *, signed_body: bytes | None = None):
    return http.post(
        "/internal/aqub/v2/delivery",
        content=body,
        headers={
            "content-type": "application/json",
            "X-AQUB-Signature": signature(signed_body if signed_body is not None else body),
        },
    )


def test_default_off_is_not_discoverable_and_touches_no_store() -> None:
    store = Store()
    with client(settings(enabled=False), store) as http:
        response = post(http, raw_command())
    assert response.status_code == 404
    assert store.documents == []


def test_exact_raw_signature_accepts_and_redacts_conversation() -> None:
    store = Store()
    body = raw_command()
    with client(settings(), store) as http:
        response = post(http, body)
    assert response.status_code == 200
    assert response.json()["ack"]["status"] == "committed"
    assert response.json()["receipt"] is None
    assert "conversation_ref" not in response.text
    assert len(store.documents) == 1


def test_tampered_bytes_fail_before_store() -> None:
    store = Store()
    signed = raw_command()
    tampered = signed.replace(b"Synthetic only", b"Tampered value")
    with client(settings(), store) as http:
        response = post(http, tampered, signed_body=signed)
    assert response.status_code == 401
    assert response.json()["detail"] == "AQUB_V2_SIGNATURE_INVALID"
    assert store.documents == []


def test_schema_valid_route_rejection_remains_a_structured_ack() -> None:
    store = Store(rejected=True)
    with client(settings(), store) as http:
        response = post(http, raw_command())
    assert response.status_code == 200
    assert response.json()["ack"]["status"] == "rejected"
    assert response.json()["ack"]["reason_code"] == "AQUB_V2_ROUTE_NOT_ACTIVE"
    assert response.json()["receipt"] is None


def test_duplicate_keys_non_finite_and_unknown_delivery_type_fail_closed() -> None:
    valid = raw_command()
    duplicate = valid.replace(
        b'{"schema_version":',
        b'{"schema_version":"aqub.delivery-command.v2","schema_version":',
        1,
    )
    non_finite = valid.replace(b'"route_generation":7', b'"route_generation":NaN')
    unknown_type = valid.replace(b"food.fact_reply", b"food.unreviewed")
    store = Store()
    with client(settings(), store) as http:
        duplicate_response = post(http, duplicate)
        non_finite_response = post(http, non_finite)
        type_response = post(http, unknown_type)
    assert duplicate_response.status_code == 422
    assert non_finite_response.status_code == 422
    assert type_response.status_code == 422
    assert store.documents == []


def test_oversized_body_and_short_secret_fail_closed() -> None:
    store = Store()
    oversized = b"{" + b"x" * AQUB_V2_MAX_JSON_BYTES + b"}"
    with client(settings(), store) as http:
        oversized_response = post(http, oversized)
    with client(settings(secret="too-short"), store) as http:
        secret_response = post(http, raw_command())
    assert oversized_response.status_code == 413
    assert secret_response.status_code == 503
    assert store.documents == []
