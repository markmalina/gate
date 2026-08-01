"""Durable SIMULATED-only transport seam for BECOME W1.

This module deliberately has no provider client.  It persists opaque callbacks,
effect claims and local outbox work so transport recovery can be tested without
messages, payments, identity payloads or external effects.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


FROZEN_DIGEST = "0303067fb38d33292a255bd6ee0dcd38ea749a5b89a1c5823fa3e8a47480f834"
PROVIDER_STATES = frozenset({"reserved", "in_flight", "settled", "unknown"})
FORBIDDEN_KEYS = frozenset(
    {
        "transcript",
        "transcript_text",
        "signed_url",
        "provider_identity",
        "provider_user_id",
        "media",
        "media_bytes",
        "phone",
        "email",
    }
)


class ReplayMutation(ValueError):
    """An idempotency key was reused with different content."""


class BlindRetryForbidden(RuntimeError):
    """An uncertain provider outcome requires reconciliation, not retry."""


@dataclass(frozen=True)
class Claim:
    claim_ref: str
    callback_ref: str
    effect_ref: str
    provider_state: str
    lease_owner: str
    lease_until: str


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return current.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    _reject_sensitive(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reject_sensitive(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"sensitive transport field forbidden: {sorted(forbidden)[0]}")
        for nested in value.values():
            _reject_sensitive(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive(nested)


class GateTransportStore:
    """SQLite-backed local inbox/outbox with one effect claim per callback."""

    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS become_transport_inbox (
              callback_ref TEXT PRIMARY KEY,
              payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
              received_at TEXT NOT NULL,
              frozen_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_effect_claim (
              claim_ref TEXT PRIMARY KEY,
              callback_ref TEXT NOT NULL UNIQUE REFERENCES become_transport_inbox(callback_ref),
              effect_ref TEXT NOT NULL UNIQUE,
              provider_state TEXT NOT NULL CHECK(provider_state IN ('reserved','in_flight','settled','unknown')),
              reconciliation_required INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_required IN (0,1)),
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_transport_outbox (
              claim_ref TEXT PRIMARY KEY REFERENCES become_effect_claim(claim_ref),
              envelope_sha256 TEXT NOT NULL CHECK(length(envelope_sha256) = 64),
              status TEXT NOT NULL CHECK(status IN ('queued','leased','settled','unknown','dead_letter')),
              lease_owner TEXT,
              lease_until TEXT,
              attempts INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_reconciliation (
              claim_ref TEXT PRIMARY KEY REFERENCES become_effect_claim(claim_ref),
              reason TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('required','resolved')),
              updated_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    def record_callback(self, callback_ref: str, payload: dict[str, Any], at: datetime | None = None) -> tuple[str, bool]:
        if not callback_ref.startswith("callback_"):
            raise ValueError("synthetic opaque callback_ref required")
        stamp = _iso(_now(at))
        payload_sha = hashlib.sha256(_canonical(payload)).hexdigest()
        suffix = hashlib.sha256(callback_ref.encode()).hexdigest()[:24]
        claim_ref, effect_ref = f"claim_{suffix}", f"effect_{suffix}"
        envelope_sha = hashlib.sha256((FROZEN_DIGEST + callback_ref + payload_sha).encode()).hexdigest()

        self._db.execute("BEGIN IMMEDIATE")
        try:
            existing = self._db.execute(
                "SELECT payload_sha256 FROM become_transport_inbox WHERE callback_ref = ?", (callback_ref,)
            ).fetchone()
            if existing:
                if existing["payload_sha256"] != payload_sha:
                    raise ReplayMutation("mutated callback replay rejected")
                self._db.execute("COMMIT")
                return claim_ref, False
            self._db.execute(
                "INSERT INTO become_transport_inbox VALUES (?, ?, ?, ?)",
                (callback_ref, payload_sha, stamp, FROZEN_DIGEST),
            )
            self._db.execute(
                "INSERT INTO become_effect_claim VALUES (?, ?, ?, 'reserved', 0, ?)",
                (claim_ref, callback_ref, effect_ref, stamp),
            )
            self._db.execute(
                "INSERT INTO become_transport_outbox VALUES (?, ?, 'queued', NULL, NULL, 0, ?)",
                (claim_ref, envelope_sha, stamp),
            )
            self._db.execute("COMMIT")
            return claim_ref, True
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def claim_next(self, owner: str, at: datetime | None = None, lease_seconds: int = 30) -> Claim | None:
        if not owner.startswith("worker_") or lease_seconds <= 0:
            raise ValueError("synthetic worker and positive lease required")
        current = _now(at)
        stamp, expires = _iso(current), _iso(current + timedelta(seconds=lease_seconds))
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                """
                SELECT o.claim_ref, c.callback_ref, c.effect_ref, c.provider_state
                FROM become_transport_outbox o JOIN become_effect_claim c USING (claim_ref)
                WHERE c.provider_state = 'reserved'
                  AND (o.status = 'queued' OR (o.status = 'leased' AND o.lease_until <= ?))
                ORDER BY c.created_at, o.claim_ref LIMIT 1
                """,
                (stamp,),
            ).fetchone()
            if not row:
                self._db.execute("COMMIT")
                return None
            self._db.execute(
                "UPDATE become_transport_outbox SET status='leased', lease_owner=?, lease_until=?, attempts=attempts+1, updated_at=? WHERE claim_ref=?",
                (owner, expires, stamp, row["claim_ref"]),
            )
            self._db.execute("COMMIT")
            return Claim(row["claim_ref"], row["callback_ref"], row["effect_ref"], "reserved", owner, expires)
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def begin_effect(self, claim_ref: str, owner: str, at: datetime | None = None) -> None:
        """Cross the local/provider seam only for the current lease owner."""
        stamp = _iso(_now(at))
        changed = self._db.execute(
            """
            UPDATE become_effect_claim SET provider_state='in_flight'
            WHERE claim_ref=? AND provider_state='reserved' AND EXISTS (
              SELECT 1 FROM become_transport_outbox o
              WHERE o.claim_ref=become_effect_claim.claim_ref
                AND o.status='leased' AND o.lease_owner=? AND o.lease_until>?
            )
            """,
            (claim_ref, owner, stamp),
        ).rowcount
        if changed != 1:
            raise ValueError("active owned lease required before in_flight")

    def reconcile_expired_in_flight(self, at: datetime | None = None) -> int:
        """Turn uncertain expired effects into reconciliation work, never retry."""
        stamp = _iso(_now(at))
        self._db.execute("BEGIN IMMEDIATE")
        try:
            rows = self._db.execute(
                """
                SELECT o.claim_ref FROM become_transport_outbox o
                JOIN become_effect_claim c USING (claim_ref)
                WHERE o.status='leased' AND o.lease_until<=? AND c.provider_state='in_flight'
                """,
                (stamp,),
            ).fetchall()
            for row in rows:
                claim_ref = row["claim_ref"]
                self._db.execute(
                    "UPDATE become_effect_claim SET provider_state='unknown', reconciliation_required=1 WHERE claim_ref=?",
                    (claim_ref,),
                )
                self._db.execute(
                    "UPDATE become_transport_outbox SET status='unknown', updated_at=? WHERE claim_ref=?",
                    (stamp, claim_ref),
                )
                self._db.execute(
                    "INSERT OR REPLACE INTO become_reconciliation VALUES (?, 'expired_in_flight_lease', 'required', ?)",
                    (claim_ref, stamp),
                )
            self._db.execute("COMMIT")
            return len(rows)
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def record_outcome(self, claim_ref: str, state: str, at: datetime | None = None) -> None:
        if state not in PROVIDER_STATES:
            raise ValueError("invalid provider state")
        stamp = _iso(_now(at))
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT provider_state FROM become_effect_claim WHERE claim_ref=?", (claim_ref,)
            ).fetchone()
            if not row:
                raise KeyError(claim_ref)
            if row["provider_state"] == "settled" and state != "settled":
                raise ValueError("settled effect is terminal")
            reconciliation = int(state == "unknown")
            self._db.execute(
                "UPDATE become_effect_claim SET provider_state=?, reconciliation_required=? WHERE claim_ref=?",
                (state, reconciliation, claim_ref),
            )
            status = "settled" if state == "settled" else "unknown" if state == "unknown" else "leased"
            self._db.execute(
                "UPDATE become_transport_outbox SET status=?, updated_at=? WHERE claim_ref=?",
                (status, stamp, claim_ref),
            )
            if state == "unknown":
                self._db.execute(
                    "INSERT OR REPLACE INTO become_reconciliation VALUES (?, 'provider_outcome_unknown', 'required', ?)",
                    (claim_ref, stamp),
                )
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def retry(self, claim_ref: str, at: datetime | None = None) -> None:
        state = self._db.execute(
            "SELECT provider_state FROM become_effect_claim WHERE claim_ref=?", (claim_ref,)
        ).fetchone()
        if not state:
            raise KeyError(claim_ref)
        if state["provider_state"] in {"in_flight", "unknown", "settled"}:
            raise BlindRetryForbidden(f"cannot blindly retry {state['provider_state']} effect")
        self._db.execute(
            "UPDATE become_transport_outbox SET status='queued', lease_owner=NULL, lease_until=NULL, updated_at=? WHERE claim_ref=?",
            (_iso(_now(at)), claim_ref),
        )

    def counts(self) -> dict[str, int]:
        return {
            name: self._db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in (
                "become_transport_inbox",
                "become_effect_claim",
                "become_transport_outbox",
                "become_reconciliation",
            )
        }
