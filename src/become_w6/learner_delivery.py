"""Duplicate-safe SIMULATED learner delivery ledger for frozen W6."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FROZEN_DIGEST = "418bb1d0e49abc1239addd0bc03502c42f6c2d5524c852b912221f3763b15487"
OPERATIONS = frozenset({"exercise_delivery", "result_receipt_delivery"})
PROVIDER_STATES = frozenset({"reserved", "in_flight", "settled", "unknown"})


class ReplayMutation(ValueError):
    pass


class BlindRetryForbidden(RuntimeError):
    pass


@dataclass(frozen=True)
class DeliveryClaim:
    delivery_ref: str
    idempotency_key: str
    operation: str
    provider_state: str
    delivery_receipt_sha256: str
    created: bool


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _opaque(value: str, prefix: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 180:
        raise ValueError(f"opaque {prefix} ref required")


class LearnerDeliveryStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS become_learner_delivery (
              delivery_ref TEXT PRIMARY KEY,
              idempotency_key TEXT NOT NULL UNIQUE,
              operation TEXT NOT NULL CHECK(operation IN ('exercise_delivery','result_receipt_delivery')),
              learner_ref TEXT NOT NULL,
              signed_link_sha256 TEXT NOT NULL CHECK(length(signed_link_sha256)=64),
              exercise_version_ref TEXT NOT NULL,
              session_ref TEXT NOT NULL,
              payload_ref TEXT NOT NULL,
              mode TEXT NOT NULL CHECK(mode='SIMULATED'),
              provider_state TEXT NOT NULL CHECK(provider_state IN ('reserved','in_flight','settled','unknown')),
              reconciliation_required INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_required IN (0,1)),
              delivery_receipt_sha256 TEXT NOT NULL CHECK(length(delivery_receipt_sha256)=64),
              mock_receipt_ref TEXT,
              frozen_digest TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_delivery_reconciliation (
              delivery_ref TEXT PRIMARY KEY REFERENCES become_learner_delivery(delivery_ref),
              status TEXT NOT NULL CHECK(status IN ('required','resolved')),
              receipt_ref TEXT,
              updated_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    def reserve(self, envelope: dict[str, str], at: datetime) -> DeliveryClaim:
        required = {
            "delivery_ref",
            "idempotency_key",
            "operation",
            "learner_ref",
            "signed_link_sha256",
            "exercise_version_ref",
            "session_ref",
            "payload_ref",
            "mode",
        }
        if set(envelope) != required or envelope["mode"] != "SIMULATED":
            raise ValueError("exact SIMULATED delivery envelope required")
        for field, prefix in (
            ("delivery_ref", "delivery_"),
            ("idempotency_key", "idempotency_"),
            ("learner_ref", "learner_"),
            ("exercise_version_ref", "exercise_version_"),
            ("session_ref", "exercise_session_"),
        ):
            _opaque(envelope[field], prefix)
        operation = envelope["operation"]
        if operation not in OPERATIONS or len(envelope["signed_link_sha256"]) != 64:
            raise ValueError("version-pinned signed delivery required")
        payload_prefix = (
            "exercise_delivery_payload_" if operation == "exercise_delivery" else "result_receipt_"
        )
        _opaque(envelope["payload_ref"], payload_prefix)
        body = {"schema": "gate.learner-delivery@1", "frozen_digest": FROZEN_DIGEST, **envelope}
        receipt_sha = _sha(body)
        existing = self._db.execute(
            """SELECT delivery_ref, operation, learner_ref, signed_link_sha256,
                      exercise_version_ref, session_ref, payload_ref, mode,
                      delivery_receipt_sha256, provider_state
                 FROM become_learner_delivery WHERE idempotency_key=?""",
            (envelope["idempotency_key"],),
        ).fetchone()
        expected = (
            envelope["delivery_ref"],
            operation,
            envelope["learner_ref"],
            envelope["signed_link_sha256"],
            envelope["exercise_version_ref"],
            envelope["session_ref"],
            envelope["payload_ref"],
            "SIMULATED",
            receipt_sha,
        )
        if existing:
            if tuple(existing)[:9] != expected:
                raise ReplayMutation("mutated duplicate delivery rejected")
            return DeliveryClaim(
                existing["delivery_ref"],
                envelope["idempotency_key"],
                operation,
                existing["provider_state"],
                existing["delivery_receipt_sha256"],
                False,
            )
        self._db.execute(
            """INSERT INTO become_learner_delivery VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, 'SIMULATED', 'reserved', 0, ?, NULL, ?, ?)""",
            (
                envelope["delivery_ref"],
                envelope["idempotency_key"],
                operation,
                envelope["learner_ref"],
                envelope["signed_link_sha256"],
                envelope["exercise_version_ref"],
                envelope["session_ref"],
                envelope["payload_ref"],
                receipt_sha,
                FROZEN_DIGEST,
                _iso(at),
            ),
        )
        return DeliveryClaim(
            envelope["delivery_ref"],
            envelope["idempotency_key"],
            operation,
            "reserved",
            receipt_sha,
            True,
        )

    def begin_mock(self, delivery_ref: str) -> None:
        changed = self._db.execute(
            "UPDATE become_learner_delivery SET provider_state='in_flight' WHERE delivery_ref=? AND provider_state='reserved'",
            (delivery_ref,),
        ).rowcount
        if changed != 1:
            raise ValueError("reserved delivery required")

    def settle_mock(self, delivery_ref: str) -> str:
        receipt_ref = (
            "delivery_mock_receipt_" + hashlib.sha256(delivery_ref.encode()).hexdigest()[:24]
        )
        changed = self._db.execute(
            "UPDATE become_learner_delivery SET provider_state='settled', mock_receipt_ref=? WHERE delivery_ref=? AND provider_state='in_flight'",
            (receipt_ref, delivery_ref),
        ).rowcount
        if changed != 1:
            raise ValueError("in-flight delivery required")
        return receipt_ref

    def mark_unknown(self, delivery_ref: str, at: datetime) -> None:
        changed = self._db.execute(
            "UPDATE become_learner_delivery SET provider_state='unknown', reconciliation_required=1 WHERE delivery_ref=? AND provider_state='in_flight'",
            (delivery_ref,),
        ).rowcount
        if changed != 1:
            raise ValueError("in-flight delivery required")
        self._db.execute(
            "INSERT OR REPLACE INTO become_delivery_reconciliation VALUES (?, 'required', NULL, ?)",
            (delivery_ref, _iso(at)),
        )

    def retry(self, delivery_ref: str) -> None:
        row = self._db.execute(
            "SELECT provider_state FROM become_learner_delivery WHERE delivery_ref=?",
            (delivery_ref,),
        ).fetchone()
        if not row:
            raise KeyError(delivery_ref)
        if row["provider_state"] in {"in_flight", "settled", "unknown"}:
            raise BlindRetryForbidden(f"cannot blindly retry {row['provider_state']} delivery")

    def reconcile(
        self, delivery_ref: str, receipt_ref: str, no_effect_proven: bool, at: datetime
    ) -> None:
        _opaque(receipt_ref, "reconciliation_receipt_")
        row = self._db.execute(
            "SELECT provider_state FROM become_learner_delivery WHERE delivery_ref=?",
            (delivery_ref,),
        ).fetchone()
        if not row or row["provider_state"] != "unknown":
            raise ValueError("unknown delivery required")
        next_state = "reserved" if no_effect_proven else "settled"
        self._db.execute(
            "UPDATE become_learner_delivery SET provider_state=?, reconciliation_required=0 WHERE delivery_ref=?",
            (next_state, delivery_ref),
        )
        self._db.execute(
            "UPDATE become_delivery_reconciliation SET status='resolved', receipt_ref=?, updated_at=? WHERE delivery_ref=?",
            (receipt_ref, _iso(at), delivery_ref),
        )

    def counts(self) -> dict[str, int]:
        return {
            "deliveries": self._db.execute(
                "SELECT count(*) FROM become_learner_delivery"
            ).fetchone()[0],
            "reconciliation": self._db.execute(
                "SELECT count(*) FROM become_delivery_reconciliation"
            ).fetchone()[0],
        }
