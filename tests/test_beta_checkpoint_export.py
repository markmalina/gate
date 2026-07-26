from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from app.infrastructure.beta_checkpoint_export import (
    EVENT_VERSION,
    HKDF_INFO,
    BetaCheckpointExportError,
    _build_portable_checkpoint,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _events(group_id: str) -> list[dict[str, object]]:
    return [
        {
            "schema_version": EVENT_VERSION,
            "event_id": "beta-event-1",
            "group_id": group_id,
            "sequence": 1,
            "kind": "message.inbound",
            "occurred_at": "2026-07-26T07:36:31.000Z",
            "recorded_at": "2026-07-26T07:36:31.100Z",
            "actor_ref": "actor-a",
            "content": {
                "format": "structured_text",
                "text": "owner smoke",
                "language": "ru",
            },
            "provenance": {
                "source": "telegram",
                "source_event_ref": "gate-event-a",
                "content_sha256": "a" * 64,
                "idempotency_key": "gate.context.a",
            },
            "supersedes": None,
            "tombstones": None,
        },
        {
            "schema_version": EVENT_VERSION,
            "event_id": "beta-event-2",
            "group_id": group_id,
            "sequence": 2,
            "kind": "message.outbound",
            "occurred_at": "2026-07-26T07:36:32.000Z",
            "recorded_at": "2026-07-26T07:36:32.100Z",
            "actor_ref": "actor-b",
            "content": {
                "format": "structured_text",
                "text": "smoke passed",
                "language": "ru",
            },
            "provenance": {
                "source": "telegram",
                "source_event_ref": "gate-event-b",
                "content_sha256": "b" * 64,
                "idempotency_key": "gate.context.b",
            },
            "supersedes": None,
            "tombstones": None,
        },
    ]


def test_checkpoint_is_node_compatible_and_restores_all_events() -> None:
    group_id = "beta-group-stroi-alphav-001"
    private_key = X25519PrivateKey.generate()
    recipient = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    events = _events(group_id)

    checkpoint = _build_portable_checkpoint(
        group_id=group_id,
        events=events,
        recipient_public_key=recipient,
        created_at="2026-07-26T07:37:00.000Z",
    )

    container = json.loads(checkpoint["container"])
    ephemeral_public = serialization.load_der_public_key(
        base64.b64decode(container["ephemeral_public_key"])
    )
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=group_id.encode(),
        info=HKDF_INFO,
    ).derive(private_key.exchange(ephemeral_public))
    header = {
        key: container[key]
        for key in (
            "schema_version",
            "group_id",
            "sequence_start",
            "sequence_end",
            "event_count",
            "created_at",
        )
    }
    aad = json.dumps(
        header, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    plaintext = AESGCM(key).decrypt(
        base64.b64decode(container["nonce"]),
        base64.b64decode(container["ciphertext"])
        + base64.b64decode(container["tag"]),
        aad,
    )
    restored = [
        json.loads(line) for line in plaintext.decode().splitlines() if line
    ]

    assert restored == events
    assert checkpoint["manifest"]["sequence_start"] == 1
    assert checkpoint["manifest"]["sequence_end"] == 2
    assert checkpoint["manifest"]["event_count"] == 2


def test_checkpoint_rejects_non_contiguous_input() -> None:
    group_id = "beta-group-stroi-alphav-001"
    private_key = X25519PrivateKey.generate()
    recipient = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    events = _events(group_id)
    events[1]["sequence"] = 3

    with pytest.raises(BetaCheckpointExportError) as exc:
        _build_portable_checkpoint(
            group_id=group_id,
            events=events,
            recipient_public_key=recipient,
            created_at=datetime.now(UTC).isoformat(),
        )

    assert exc.value.code == "beta_checkpoint_context_not_contiguous"
