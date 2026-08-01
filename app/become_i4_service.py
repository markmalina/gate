"""Provider-disabled internal BECOME I4 transport/payment HTTP service."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import unicodedata
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


CONTRACT_DIGEST = "91c3a210709c63738358bd0c512d11a9668acabd0997afcd68ba240bed38f573"
IC9_DIGEST = "e8b30118dbd754a841e575b0bc3cb8681461381b1b138d2fa1b3a75c28e2a5e7"
REQUEST_SCHEMA = "become.i4-service-request@1"
RECEIPT_SCHEMA = "become.i4-service-receipt@1"
SERVICE = "gate"

_REF_RE = re.compile(r"^synthetic://[a-z0-9/_-]+$")
_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_REQUEST_FIELDS = frozenset(
    {"schema", "contract_digest", "mode", "quest_ref", "correlation_ref", "subject_ref", "input"}
)
_INPUT_FIELDS = frozenset(
    {"result_receipt_ref", "delivery_ref", "continuation", "amount", "currency"}
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema", "service", "source_sha", "contract_digest", "mode", "quest_ref",
        "correlation_ref", "subject_ref", "external_effects", "output", "receipt_digest",
    }
)
_OUTPUT_FIELDS = frozenset(
    {
        "delivery_receipt_ref", "payment_receipt_ref", "entitlement_receipt_ref",
        "group_delivery_receipt_ref", "referral_delivery_receipt_ref", "amount", "currency",
        "successful_payment", "result_before_payment", "provider_states", "blind_retry_attempts",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "media_bytes", "media_content", "pii", "provider_id", "provider_payload",
        "raw_transcript", "secret", "signed_url", "telegram_id", "token", "transcript_text",
    }
)
_DISABLED_ENV = {
    "BECOME_INTERNAL_ONLY": "true",
    "BECOME_RUNTIME_MODE": "SIMULATED_TEST",
    "BECOME_LIVE_ENABLED": "false",
    "BECOME_PROVIDER_SEND_ENABLED": "false",
    "BECOME_PAYMENT_ENABLED": "false",
    "BECOME_PUBLICATION_ENABLED": "false",
    "BECOME_MEDIA_DOWNLOAD_ENABLED": "false",
    "BECOME_EXTERNAL_EFFECTS_ENABLED": "false",
    "BECOME_I4_CONTRACT_DIGEST": CONTRACT_DIGEST,
    "BECOME_IC9_DIGEST": IC9_DIGEST,
}


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _reject_sensitive(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            normalized = unicodedata.normalize("NFKC", key).casefold()
            if normalized in _SENSITIVE_KEYS:
                raise ValueError("sensitive field rejected")
            _reject_sensitive(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive(nested)


def _require_ref(value: object, field: str) -> str:
    if not isinstance(value, str) or _REF_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be an opaque synthetic reference")
    return value


def _require_source_sha(value: object) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError("BECOME_SOURCE_SHA must be an exact lowercase git SHA")
    return value


def runtime_source_sha() -> str:
    for key, expected in _DISABLED_ENV.items():
        if os.environ.get(key) != expected:
            raise RuntimeError(f"provider-disabled runtime requires {key}={expected}")
    return _require_source_sha(os.environ.get("BECOME_SOURCE_SHA"))


def validate_request(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _REQUEST_FIELDS:
        raise ValueError("exact I4 request envelope required")
    _reject_sensitive(value)
    request = dict(value)
    if request["schema"] != REQUEST_SCHEMA:
        raise ValueError("request schema mismatch")
    if request["contract_digest"] != CONTRACT_DIGEST:
        raise ValueError("contract digest mismatch")
    if request["mode"] not in {"SIMULATED", "TEST"}:
        raise ValueError("SIMULATED or TEST mode required")
    for field in ("quest_ref", "correlation_ref", "subject_ref"):
        _require_ref(request[field], field)

    domain_input = request["input"]
    if not isinstance(domain_input, Mapping) or set(domain_input) != _INPUT_FIELDS:
        raise ValueError("exact Gate I4 input required")
    _require_ref(domain_input["result_receipt_ref"], "result_receipt_ref")
    _require_ref(domain_input["delivery_ref"], "delivery_ref")
    if domain_input["continuation"] != "PAID_TEST":
        raise ValueError("PAID_TEST continuation required")
    if type(domain_input["amount"]) is not int or domain_input["amount"] != 1:
        raise ValueError("exactly 1 XTR required")
    if domain_input["currency"] != "XTR":
        raise ValueError("XTR currency required")
    return request


def _receipt_ref(kind: str, request: Mapping[str, object]) -> str:
    identity = canonical_digest(
        {"kind": kind, "request": request, "contract_digest": CONTRACT_DIGEST, "ic9_digest": IC9_DIGEST}
    )
    return f"synthetic://gate/{kind}/{identity[:24]}"


def build_receipt(value: object, *, source_sha: str) -> dict[str, Any]:
    request = validate_request(value)
    exact_source_sha = _require_source_sha(source_sha)
    output: dict[str, object] = {
        "delivery_receipt_ref": _receipt_ref("delivery-receipt", request),
        "payment_receipt_ref": _receipt_ref("stars-test-receipt", request),
        "entitlement_receipt_ref": _receipt_ref("entitlement-receipt", request),
        "group_delivery_receipt_ref": _receipt_ref("group-delivery-receipt", request),
        "referral_delivery_receipt_ref": _receipt_ref("referral-delivery-receipt", request),
        "amount": 1,
        "currency": "XTR",
        "successful_payment": True,
        "result_before_payment": True,
        "provider_states": ["reserved", "in_flight", "unknown", "settled"],
        "blind_retry_attempts": 0,
    }
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "service": SERVICE,
        "source_sha": exact_source_sha,
        "contract_digest": CONTRACT_DIGEST,
        "mode": request["mode"],
        "quest_ref": request["quest_ref"],
        "correlation_ref": request["correlation_ref"],
        "subject_ref": request["subject_ref"],
        "external_effects": 0,
        "output": output,
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def validate_receipt(receipt: object, *, request: object, expected_source_sha: str) -> bool:
    if not isinstance(receipt, Mapping) or set(receipt) != _RECEIPT_FIELDS:
        raise ValueError("exact I4 service receipt required")
    _reject_sensitive(receipt)
    if not isinstance(receipt["output"], Mapping) or set(receipt["output"]) != _OUTPUT_FIELDS:
        raise ValueError("exact Gate I4 output required")
    expected = build_receipt(request, source_sha=expected_source_sha)
    if dict(receipt) != expected:
        raise ValueError("Gate I4 receipt does not match frozen request")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    return hmac.compare_digest(receipt["receipt_digest"], canonical_digest(unsigned))


class Handler(BaseHTTPRequestHandler):
    server_version = "become-i4-gate/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: int, value: object) -> None:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self._json(404, {"error": "not_found"})
            return
        try:
            source_sha = runtime_source_sha()
        except (RuntimeError, ValueError):
            self._json(503, {"error": "runtime_not_provider_disabled"})
            return
        self._json(200, {"status": "ok", "service": SERVICE, "source_sha": source_sha,
                         "contract_digest": CONTRACT_DIGEST, "mode": "SIMULATED_TEST",
                         "external_effects": 0})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/internal/become/i4":
            self._json(404, {"error": "not_found"})
            return
        try:
            source_sha = runtime_source_sha()
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 16_384:
                raise ValueError("bounded JSON body required")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("application/json required")
            request = json.loads(self.rfile.read(content_length))
            self._json(200, build_receipt(request, source_sha=source_sha))
        except (json.JSONDecodeError, RuntimeError, ValueError):
            self._json(400, {"error": "invalid_request"})


def create_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    runtime_source_sha()
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    server = create_server(os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8000")))
    server.serve_forever()


if __name__ == "__main__":
    main()
