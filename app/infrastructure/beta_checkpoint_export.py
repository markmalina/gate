"""Encrypted, provider-neutral manual checkpoint export for a Beta Space."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
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
MAX_CHECKPOINT_EVENTS = 50
MAX_EXPORT_EVENTS = MAX_CHECKPOINT_EVENTS
MAX_EVENT_CONTENT_CODE_POINTS = 65_536
MAX_EVENT_SEQUENCE = 999_999_999_999
MAX_PLAINTEXT_BYTES = 2_000_000

_EVENT_KEYS = frozenset(
    {
        "schema_version",
        "event_id",
        "group_id",
        "sequence",
        "kind",
        "occurred_at",
        "recorded_at",
        "actor_ref",
        "content",
        "provenance",
        "supersedes",
        "tombstones",
    }
)
_CONTENT_KEYS = frozenset({"format", "text", "language"})
_PROVENANCE_KEYS = frozenset(
    {"source", "source_event_ref", "content_sha256", "idempotency_key"}
)
_EVENT_KINDS = frozenset(
    {
        "message.inbound",
        "message.outbound",
        "fact",
        "decision",
        "correction",
        "tombstone",
        "boundary",
    }
)
_CONTENT_FORMATS = frozenset({"structured_text", "system"})
_PROVENANCE_SOURCES = frozenset({"telegram", "beta", "import"})
_EVENT_ID = re.compile(r"^beta-event-[a-f0-9]{32}$")
_GROUP_ID = re.compile(r"^beta-group-[a-z0-9-]+-[0-9]{3}$")
_ACTOR_REF = re.compile(r"^actor-[a-f0-9]{16,64}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_CANONICAL_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:"
    r"[0-5][0-9]\.[0-9]{3}Z$"
)


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

    binding = await session.scalar(
        select(BetaContextBindingModel).where(
            BetaContextBindingModel.installation_ref == installation_ref,
            BetaContextBindingModel.state == "active",
        )
    )
    if binding is None:
        raise BetaCheckpointExportError("beta_checkpoint_binding_not_active")
    if not binding.checkpoint_recipient_pem:
        raise BetaCheckpointExportError("beta_checkpoint_recipient_missing")

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
        recipient_public_key=binding.checkpoint_recipient_pem.encode("utf-8"),
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
    _validate_portable_event_stream(group_id=group_id, events=events)
    if not _is_canonical_timestamp(created_at):
        raise BetaCheckpointExportError("beta_checkpoint_timestamp_invalid")
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


def _byte_bounded_event_prefix(
    *,
    group_id: str,
    events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the largest canonical prefix that fits the plaintext limit.

    Validation runs before sizing so an invalid later event cannot be hidden by
    an earlier byte boundary.  A singleton over the limit is an explicit
    terminal protocol error; canonical v1's per-event code-point bound keeps
    that state unreachable for valid current events.
    """

    _validate_portable_event_stream(group_id=group_id, events=events)
    selected: list[dict[str, Any]] = []
    total_bytes = 0
    for event in events:
        try:
            event_bytes = len((_stable_json(event) + "\n").encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise BetaCheckpointExportError(
                "beta_checkpoint_event_invalid"
            ) from exc
        if event_bytes > MAX_PLAINTEXT_BYTES:
            raise BetaCheckpointExportError(
                "beta_checkpoint_event_oversized"
            )
        if total_bytes + event_bytes > MAX_PLAINTEXT_BYTES:
            break
        selected.append(event)
        total_bytes += event_bytes
    if not selected:
        raise BetaCheckpointExportError("beta_checkpoint_event_oversized")
    return selected


def _assert_contiguous_from_origin(events: Sequence[dict[str, Any]]) -> None:
    if not events:
        raise BetaCheckpointExportError("beta_checkpoint_context_empty")
    group_id = events[0].get("group_id")
    if not isinstance(group_id, str):
        raise BetaCheckpointExportError("beta_checkpoint_event_invalid")
    _validate_portable_event_stream(group_id=group_id, events=events)


def _validate_portable_event_stream(
    *,
    group_id: str,
    events: Sequence[dict[str, Any]],
) -> None:
    """Validate the exact Beta v1 plaintext contract without mutating it."""

    if not events:
        raise BetaCheckpointExportError("beta_checkpoint_context_empty")
    if len(events) > MAX_EXPORT_EVENTS:
        raise BetaCheckpointExportError("beta_checkpoint_event_limit_exceeded")
    if not isinstance(group_id, str) or _GROUP_ID.fullmatch(group_id) is None:
        raise BetaCheckpointExportError("beta_checkpoint_event_invalid")

    first_event = events[0]
    if not isinstance(first_event, dict):
        raise BetaCheckpointExportError("beta_checkpoint_event_invalid")
    first_sequence = first_event.get("sequence")
    if (
        type(first_sequence) is not int
        or first_sequence < 1
        or first_sequence > MAX_EVENT_SEQUENCE
    ):
        raise BetaCheckpointExportError("beta_checkpoint_context_not_contiguous")

    event_ids: set[str] = set()
    batch_event_ids = {
        event.get("event_id")
        for event in events
        if isinstance(event, dict)
        and isinstance(event.get("event_id"), str)
    }
    idempotency_keys: set[str] = set()
    source_event_refs: set[str] = set()
    for offset, event in enumerate(events):
        _validate_portable_event(event)
        event_id = event["event_id"]
        sequence = event["sequence"]
        provenance = event["provenance"]
        assert isinstance(event_id, str)
        assert type(sequence) is int
        assert isinstance(provenance, dict)
        idempotency_key = provenance["idempotency_key"]
        source_event_ref = provenance["source_event_ref"]
        assert isinstance(idempotency_key, str)
        assert isinstance(source_event_ref, str)
        target = event["supersedes"] or event["tombstones"]

        if (
            event["group_id"] != group_id
            or sequence != first_sequence + offset
            or event_id in event_ids
            or idempotency_key in idempotency_keys
            or source_event_ref in source_event_refs
            or (
                target is not None
                and (
                    (first_sequence == 1 and target not in event_ids)
                    or (target in batch_event_ids and target not in event_ids)
                )
            )
        ):
            raise BetaCheckpointExportError(
                "beta_checkpoint_context_not_contiguous"
            )
        event_ids.add(event_id)
        idempotency_keys.add(idempotency_key)
        source_event_refs.add(source_event_ref)


def _validate_portable_event(event: dict[str, Any]) -> None:
    if not isinstance(event, dict) or set(event) != _EVENT_KEYS:
        raise BetaCheckpointExportError("beta_checkpoint_event_invalid")

    content = event.get("content")
    provenance = event.get("provenance")
    if (
        not isinstance(content, dict)
        or set(content) != _CONTENT_KEYS
        or not isinstance(provenance, dict)
        or set(provenance) != _PROVENANCE_KEYS
    ):
        raise BetaCheckpointExportError("beta_checkpoint_event_invalid")

    text_value = content.get("text")
    language = content.get("language")
    supersedes = event.get("supersedes")
    tombstones = event.get("tombstones")
    kind = event.get("kind")
    sequence = event.get("sequence")
    source_event_ref = provenance.get("source_event_ref")
    content_sha256 = provenance.get("content_sha256")
    idempotency_key = provenance.get("idempotency_key")
    if kind == "correction":
        target_shape_valid = isinstance(supersedes, str) and tombstones is None
    elif kind == "tombstone":
        target_shape_valid = isinstance(tombstones, str) and supersedes is None
    else:
        target_shape_valid = supersedes is None and tombstones is None

    if (
        event.get("schema_version") != EVENT_VERSION
        or not _matches(_EVENT_ID, event.get("event_id"))
        or not _matches(_GROUP_ID, event.get("group_id"))
        or type(sequence) is not int
        or sequence < 1
        or sequence > MAX_EVENT_SEQUENCE
        or not isinstance(kind, str)
        or kind not in _EVENT_KINDS
        or not _is_canonical_timestamp(event.get("occurred_at"))
        or not _is_canonical_timestamp(event.get("recorded_at"))
        or not _matches(_ACTOR_REF, event.get("actor_ref"))
        or not isinstance(content.get("format"), str)
        or content.get("format") not in _CONTENT_FORMATS
        or not isinstance(text_value, str)
        or len(text_value) > MAX_EVENT_CONTENT_CODE_POINTS
        or not (
            language is None
            or (isinstance(language, str) and len(language) <= 32)
        )
        or not isinstance(provenance.get("source"), str)
        or provenance.get("source") not in _PROVENANCE_SOURCES
        or not isinstance(source_event_ref, str)
        or not 8 <= len(source_event_ref) <= 128
        or not _matches(_SHA256, content_sha256)
        or not _matches(_IDEMPOTENCY_KEY, idempotency_key)
        or not _nullable_event_id(supersedes)
        or not _nullable_event_id(tombstones)
        or not target_shape_valid
    ):
        raise BetaCheckpointExportError("beta_checkpoint_event_invalid")

    try:
        expected_sha256 = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as exc:
        raise BetaCheckpointExportError("beta_checkpoint_event_invalid") from exc
    if content_sha256 != expected_sha256:
        raise BetaCheckpointExportError("beta_checkpoint_event_invalid")


def _matches(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _nullable_event_id(value: Any) -> bool:
    return value is None or _matches(_EVENT_ID, value)


def _is_canonical_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    match = _CANONICAL_TIMESTAMP.fullmatch(value)
    if match is None:
        return False
    year, month, day, hour, minute, second = (
        int(part)
        for part in (
            value[0:4],
            value[5:7],
            value[8:10],
            value[11:13],
            value[14:16],
            value[17:19],
        )
    )
    leap_year = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days_by_month = (
        31,
        29 if leap_year else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    )
    return (
        1 <= month <= 12
        and 1 <= day <= days_by_month[month - 1]
        and 0 <= hour <= 23
        and 0 <= minute <= 59
        and 0 <= second <= 59
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
