"""Closed, canonical AQUB consent-revocation cancellation envelope.

The transport authenticates these canonical bytes before the repository-only
Gate apply method is called.  This module is intentionally stdlib-only so the
same validator can be exercised by a cross-repository subprocess rehearsal.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

SCHEMA_VERSION = "aqub.consent-cancellation.v1"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_CONSENT_REF_RE = re.compile(r"^consent:[A-Za-z][A-Za-z0-9._~-]{7,127}$")
_CANCELLATION_REF_RE = re.compile(r"^cancel:[A-Za-z][A-Za-z0-9._~-]{7,127}$")
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-5][0-9]\.[0-9]{3}Z$"
)
_KEYS = frozenset(
    {
        "schema_version",
        "cancellation_id",
        "cancellation_ref",
        "command_id",
        "command_sha256",
        "revoked_consent_ref",
        "revoked_authority_epoch",
        "observed_at",
    }
)


class AQUBV2ConsentCancellationError(ValueError):
    code = "AQUB_V2_CONSENT_CANCELLATION_INVALID"


def _now_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True, slots=True)
class AQUBV2ConsentCancellation:
    cancellation_id: UUID
    cancellation_ref: str
    command_id: UUID
    command_sha256: str
    revoked_consent_ref: str
    revoked_authority_epoch: int
    observed_at: datetime

    def to_wire(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "cancellation_id": str(self.cancellation_id),
            "cancellation_ref": self.cancellation_ref,
            "command_id": str(self.command_id),
            "command_sha256": self.command_sha256,
            "revoked_consent_ref": self.revoked_consent_ref,
            "revoked_authority_epoch": self.revoked_authority_epoch,
            "observed_at": _now_iso(self.observed_at),
        }

    def sql_parameters(self) -> dict[str, object]:
        return {
            "cancellation_id": self.cancellation_id,
            "cancellation_ref": self.cancellation_ref,
            "command_id": self.command_id,
            "command_sha256": self.command_sha256,
            "revoked_consent_ref": self.revoked_consent_ref,
            "revoked_authority_epoch": self.revoked_authority_epoch,
            "observed_at": self.observed_at,
        }


def parse_aqub_v2_consent_cancellation(
    value: Mapping[str, Any],
) -> AQUBV2ConsentCancellation:
    try:
        if set(value) != _KEYS or value["schema_version"] != SCHEMA_VERSION:
            raise ValueError
        if any(
            type(value[key]) is not str
            for key in (
                "cancellation_id",
                "cancellation_ref",
                "command_id",
                "command_sha256",
                "revoked_consent_ref",
                "observed_at",
            )
        ):
            raise ValueError
        command_id = UUID(value["command_id"])
        cancellation_id = UUID(value["cancellation_id"])
        if (
            str(command_id) != value["command_id"]
            or str(cancellation_id) != value["cancellation_id"]
        ):
            raise ValueError
        expected_ref = f"cancel:a{str(cancellation_id).replace('-', '')}"
        epoch = value["revoked_authority_epoch"]
        observed_at_value = value["observed_at"]
        if (
            not _CANCELLATION_REF_RE.fullmatch(value["cancellation_ref"])
            or value["cancellation_ref"] != expected_ref
            or not _HEX64_RE.fullmatch(value["command_sha256"])
            or not _CONSENT_REF_RE.fullmatch(value["revoked_consent_ref"])
            or type(epoch) is not int
            or epoch <= 0
            or not _TIMESTAMP_RE.fullmatch(observed_at_value)
        ):
            raise ValueError
        observed_at = datetime.fromisoformat(observed_at_value.replace("Z", "+00:00"))
        if _now_iso(observed_at) != observed_at_value:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise AQUBV2ConsentCancellationError from exc
    return AQUBV2ConsentCancellation(
        cancellation_id=cancellation_id,
        cancellation_ref=value["cancellation_ref"],
        command_id=command_id,
        command_sha256=value["command_sha256"],
        revoked_consent_ref=value["revoked_consent_ref"],
        revoked_authority_epoch=epoch,
        observed_at=observed_at,
    )


def serialize_aqub_v2_consent_cancellation(value: Mapping[str, Any]) -> bytes:
    envelope = parse_aqub_v2_consent_cancellation(value)
    return json.dumps(
        envelope.to_wire(),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def cancellation_evidence_ref(
    envelope: AQUBV2ConsentCancellation,
    *,
    kind: str,
) -> str:
    if kind not in {"delivery", "transport", "reconcile"}:
        raise ValueError("unsupported AQUB cancellation evidence kind")
    digest = hashlib.sha256(
        f"{kind}:{envelope.cancellation_id}:{envelope.cancellation_ref}".encode()
    ).hexdigest()
    prefix = "reconcile" if kind == "reconcile" else "receipt"
    return f"{prefix}:a{digest}"
