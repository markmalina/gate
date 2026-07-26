"""Encrypted, provider-neutral manual checkpoint export for a Beta Space."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.beta_context import BetaContextBindingModel, BetaContextEventModel

CONTAINER_VERSION = "beta.x25519-aes256gcm@1"
MANIFEST_VERSION = "beta.portable-checkpoint-manifest@1"
EVENT_VERSION = "beta.context-event@1"
HKDF_INFO = b"beta.portable-checkpoint@1"
MAX_EXPORT_EVENTS = 500
MAX_PLAINTEXT_BYTES = 2_000_000

_ALEXANDER_INSTALLATION_REF = "installation-7fbf02145be55011ae8307e57dd8216c"
_ALEXANDER_USER_WORKSPACE_ID = "beta-user-mark-001"
_ALEXANDER_GROUP_ID = "beta-group-stroi-alphav-001"
_ALEXANDER_RECIPIENT_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VuAyEAJOsuz1/C1RbIFfazec4EdZlXFFWln8UdWPJ1jddC3Fo=
-----END PUBLIC KEY-----
"""


class BetaCheckpointExportError(RuntimeError):
    """Fail-closed public error with no private context in its message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def export_beta_space_checkpoint(
    session: AsyncSession,
    *,
    installation_ref: str,
) -> dict[str, Any]:
    """Return one encrypted checkpoint without changing an export cursor."""

    if installation_ref != _ALEXANDER_INSTALLATION_REF:
        raise BetaCheckpointExportError("beta_checkpoint_export_not_allowed")

    binding = await session.scalar(
        select(BetaContextBindingModel).where(
            BetaContextBindingModel.installation_ref == installation_ref,
            BetaContextBindingModel.state == "active",
        )
    )
    if binding is None:
        raise BetaCheckpointExportError("beta_checkpoint_binding_not_active")
    if (
        binding.user_workspace_id != _ALEXANDER_USER_WORKSPACE_ID
        or binding.group_id != _ALEXANDER_GROUP_ID
    ):
        raise BetaCheckpointExportError("beta_checkpoint_binding_not_exact")

    rows = (
        await session.execute(
            select(BetaContextEventModel)
            .where(
                BetaContextEventModel.installation_id
                == binding.installation_id,
                BetaContextEventModel.group_id == binding.group_id,
            )
            .order_by(BetaContextEventModel.sequence)
            .limit(MAX_EXPORT_EVENTS + 1)
        )
    ).scalars().all()
    if not rows:
        raise BetaCheckpointExportError("beta_checkpoint_context_empty")
    if len(rows) > MAX_EXPORT_EVENTS:
        raise BetaCheckpointExportError("beta_checkpoint_event_limit_exceeded")

    events = [_portable_event(row) for row in rows]
    _assert_contiguous_from_origin(events)
    checkpoint = _build_portable_checkpoint(
        group_id=binding.group_id,
        events=events,
        recipient_public_key=_ALEXANDER_RECIPIENT_PEM,
        created_at=_iso_datetime(datetime.now(UTC)),
    )
    return {
        "installation_ref": installation_ref,
        "group_id": binding.group_id,
        "sequence_start": checkpoint["manifest"]["sequence_start"],
        "sequence_end": checkpoint["manifest"]["sequence_end"],
        "event_count": checkpoint["manifest"]["event_count"],
        "container_encoding": "base64",
        "container_base64": base64.b64encode(checkpoint["container"]).decode(
            "ascii"
        ),
        "manifest": checkpoint["manifest"],
        "plaintext_context_exposed": False,
        "telegram_private_data_exposed": False,
    }


def _portable_event(row: BetaContextEventModel) -> dict[str, Any]:
    return {
        "schema_version": EVENT_VERSION,
        "event_id": row.event_id,
        "group_id": row.group_id,
        "sequence": row.sequence,
        "kind": row.kind,
        "occurred_at": _iso_datetime(row.occurred_at),
        "recorded_at": _iso_datetime(row.recorded_at),
        "actor_ref": row.actor_ref,
        "content": row.content,
        "provenance": {
            "source": "telegram",
            "source_event_ref": row.source_event_ref,
            "content_sha256": row.content_sha256,
            "idempotency_key": row.idempotency_key,
        },
        "supersedes": None,
        "tombstones": None,
    }


def _build_portable_checkpoint(
    *,
    group_id: str,
    events: Sequence[dict[str, Any]],
    recipient_public_key: bytes,
    created_at: str,
) -> dict[str, Any]:
    _assert_contiguous_from_origin(events)
    try:
        recipient = serialization.load_pem_public_key(recipient_public_key)
    except (TypeError, ValueError) as exc:
        raise BetaCheckpointExportError(
            "beta_checkpoint_recipient_invalid"
        ) from exc
    if not isinstance(recipient, X25519PublicKey):
        raise BetaCheckpointExportError("beta_checkpoint_recipient_invalid")

    header = {
        "schema_version": CONTAINER_VERSION,
        "group_id": group_id,
        "sequence_start": events[0]["sequence"],
        "sequence_end": events[-1]["sequence"],
        "event_count": len(events),
        "created_at": created_at,
    }
    plaintext = (
        "\n".join(_stable_json(event) for event in events) + "\n"
    ).encode("utf-8")
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise BetaCheckpointExportError("beta_checkpoint_size_limit_exceeded")

    ephemeral_private = X25519PrivateKey.generate()
    shared_secret = ephemeral_private.exchange(recipient)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=group_id.encode("utf-8"),
        info=HKDF_INFO,
    ).derive(shared_secret)
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(
        nonce,
        plaintext,
        _stable_json(header).encode("utf-8"),
    )
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    ephemeral_public_der = ephemeral_private.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    container_value = {
        **header,
        "ephemeral_public_key": base64.b64encode(
            ephemeral_public_der
        ).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
    }
    container = (_stable_json(container_value) + "\n").encode("utf-8")
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "group_id": group_id,
        "sequence_start": header["sequence_start"],
        "sequence_end": header["sequence_end"],
        "event_count": header["event_count"],
        "container": CONTAINER_VERSION,
        "ciphertext_sha256": hashlib.sha256(container).hexdigest(),
        "created_at": created_at,
    }
    return {"container": container, "manifest": manifest}


def _assert_contiguous_from_origin(events: Sequence[dict[str, Any]]) -> None:
    if not events:
        raise BetaCheckpointExportError("beta_checkpoint_context_empty")
    group_id = events[0].get("group_id")
    for expected_sequence, event in enumerate(events, start=1):
        if (
            event.get("schema_version") != EVENT_VERSION
            or event.get("group_id") != group_id
            or event.get("sequence") != expected_sequence
        ):
            raise BetaCheckpointExportError(
                "beta_checkpoint_context_not_contiguous"
            )


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise BetaCheckpointExportError("beta_checkpoint_timestamp_invalid")
    return (
        value.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
