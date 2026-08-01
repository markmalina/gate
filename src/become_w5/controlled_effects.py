"""Controlled SIMULATED Instagram and Telegram-group effect ledger for W5."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FROZEN_DIGEST = "f47de6ca0a085ccc17c50c269d94b86d009972abaeab6ae9ef1c1d4b306d6419"
PROVIDER_STATES = frozenset({"reserved", "in_flight", "settled", "unknown"})
OPERATIONS = frozenset({"instagram_publish", "group_invite", "group_welcome", "group_pin"})


class ReplayMutation(ValueError):
    pass


class MissingPermission(PermissionError):
    pass


class BlindRetryForbidden(RuntimeError):
    pass


@dataclass(frozen=True)
class EffectClaim:
    effect_ref: str
    idempotency_key: str
    operation: str
    provider_state: str
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
    if not value.startswith(prefix) or len(value) > 180:
        raise ValueError(f"opaque {prefix} ref required")


def verify_staging_receipt(receipt: dict[str, str]) -> bool:
    required = {
        "schema",
        "mode",
        "frozen_digest",
        "lease_ref",
        "staging_ref",
        "account_ref",
        "render_digest",
        "caption_digest",
        "approval_package_ref",
        "mode_grant_ref",
        "expires_at",
        "staging_receipt_sha256",
    }
    if set(receipt) != required or receipt["schema"] != "take.staging-receipt@1":
        return False
    body = {key: value for key, value in receipt.items() if key != "staging_receipt_sha256"}
    return receipt["staging_receipt_sha256"] == _sha(body)


class ControlledEffectStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS become_effect_grant (
              grant_ref TEXT PRIMARY KEY,
              account_ref TEXT NOT NULL,
              render_digest TEXT NOT NULL,
              caption_digest TEXT NOT NULL,
              mode TEXT NOT NULL CHECK(mode='SIMULATED'),
              operations_json TEXT NOT NULL,
              group_ref TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_controlled_effect (
              effect_ref TEXT PRIMARY KEY,
              idempotency_key TEXT NOT NULL UNIQUE,
              operation TEXT NOT NULL,
              grant_ref TEXT NOT NULL REFERENCES become_effect_grant(grant_ref),
              staging_receipt_sha256 TEXT NOT NULL,
              provider_state TEXT NOT NULL CHECK(provider_state IN ('reserved','in_flight','settled','unknown')),
              reconciliation_required INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_required IN (0,1)),
              mock_receipt_ref TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_effect_blocker (
              blocker_ref TEXT PRIMARY KEY,
              operation TEXT NOT NULL,
              reason TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_effect_reconciliation (
              effect_ref TEXT PRIMARY KEY REFERENCES become_controlled_effect(effect_ref),
              status TEXT NOT NULL CHECK(status IN ('required','resolved')),
              receipt_ref TEXT,
              updated_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    def register_grant(self, grant: dict[str, Any], at: datetime) -> bool:
        expected_keys = {
            "grant_ref",
            "account_ref",
            "render_digest",
            "caption_digest",
            "mode",
            "operations",
            "group_ref",
        }
        if set(grant) != expected_keys or grant["mode"] != "SIMULATED":
            raise ValueError("exact SIMULATED grant required")
        _opaque(grant["grant_ref"], "mode_grant_")
        _opaque(grant["account_ref"], "account_")
        if len(grant["render_digest"]) != 64 or len(grant["caption_digest"]) != 64:
            raise ValueError("exact grant digests required")
        operations = sorted(set(grant["operations"]))
        if not operations or not set(operations).issubset(OPERATIONS):
            raise ValueError("bounded operations required")
        if any(op.startswith("group_") for op in operations):
            _opaque(grant["group_ref"], "group_")
        packed = json.dumps(operations, separators=(",", ":"))
        existing = self._db.execute(
            "SELECT account_ref, render_digest, caption_digest, mode, operations_json, group_ref FROM become_effect_grant WHERE grant_ref=?",
            (grant["grant_ref"],),
        ).fetchone()
        expected = (
            grant["account_ref"],
            grant["render_digest"],
            grant["caption_digest"],
            "SIMULATED",
            packed,
            grant["group_ref"],
        )
        if existing:
            if tuple(existing) != expected:
                raise ReplayMutation("mutated grant rejected")
            return False
        self._db.execute(
            "INSERT INTO become_effect_grant VALUES (?, ?, ?, ?, 'SIMULATED', ?, ?, ?)",
            (
                grant["grant_ref"],
                grant["account_ref"],
                grant["render_digest"],
                grant["caption_digest"],
                packed,
                grant["group_ref"],
                _iso(at),
            ),
        )
        return True

    def reserve(
        self,
        effect_ref: str,
        idempotency_key: str,
        operation: str,
        grant_ref: str,
        staging_receipt: dict[str, str],
        at: datetime,
    ) -> EffectClaim:
        _opaque(effect_ref, "effect_")
        _opaque(idempotency_key, "idempotency_")
        if operation not in OPERATIONS or not verify_staging_receipt(staging_receipt):
            raise ValueError("valid controlled effect and staging receipt required")
        if (
            staging_receipt["frozen_digest"] != FROZEN_DIGEST
            or staging_receipt["mode"] != "SIMULATED"
        ):
            raise ValueError("frozen SIMULATED staging required")
        if staging_receipt["expires_at"] <= _iso(at):
            raise MissingPermission("staging lease expired")
        grant = self._db.execute(
            "SELECT * FROM become_effect_grant WHERE grant_ref=?", (grant_ref,)
        ).fetchone()
        required_match = (
            grant
            and all(
                grant[field] == staging_receipt[field]
                for field in ("account_ref", "render_digest", "caption_digest")
            )
            and grant["mode"] == staging_receipt["mode"]
            and grant_ref == staging_receipt["mode_grant_ref"]
        )
        operations = set(json.loads(grant["operations_json"])) if grant else set()
        if not required_match or operation not in operations:
            blocker_ref = (
                "blocker_" + hashlib.sha256((idempotency_key + operation).encode()).hexdigest()[:24]
            )
            self._db.execute(
                "INSERT OR IGNORE INTO become_effect_blocker VALUES (?, ?, 'missing_exact_permission', ?)",
                (blocker_ref, operation, _iso(at)),
            )
            raise MissingPermission(blocker_ref)
        if operation.startswith("group_") and not grant["group_ref"]:
            raise MissingPermission("group_rights_missing")
        existing = self._db.execute(
            "SELECT effect_ref, operation, grant_ref, staging_receipt_sha256, provider_state FROM become_controlled_effect WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        expected = (effect_ref, operation, grant_ref, staging_receipt["staging_receipt_sha256"])
        if existing:
            if tuple(existing)[:4] != expected:
                raise ReplayMutation("mutated effect replay rejected")
            return EffectClaim(
                effect_ref, idempotency_key, operation, existing["provider_state"], False
            )
        self._db.execute(
            "INSERT INTO become_controlled_effect VALUES (?, ?, ?, ?, ?, 'reserved', 0, NULL, ?)",
            (
                effect_ref,
                idempotency_key,
                operation,
                grant_ref,
                staging_receipt["staging_receipt_sha256"],
                _iso(at),
            ),
        )
        return EffectClaim(effect_ref, idempotency_key, operation, "reserved", True)

    def begin_mock(self, effect_ref: str) -> None:
        changed = self._db.execute(
            "UPDATE become_controlled_effect SET provider_state='in_flight' WHERE effect_ref=? AND provider_state='reserved'",
            (effect_ref,),
        ).rowcount
        if changed != 1:
            raise ValueError("reserved mock effect required")

    def settle_mock(self, effect_ref: str) -> str:
        receipt = "provider_mock_receipt_" + hashlib.sha256(effect_ref.encode()).hexdigest()[:24]
        changed = self._db.execute(
            "UPDATE become_controlled_effect SET provider_state='settled', mock_receipt_ref=? WHERE effect_ref=? AND provider_state='in_flight'",
            (receipt, effect_ref),
        ).rowcount
        if changed != 1:
            raise ValueError("in-flight mock effect required")
        return receipt

    def mark_unknown(self, effect_ref: str, at: datetime) -> None:
        changed = self._db.execute(
            "UPDATE become_controlled_effect SET provider_state='unknown', reconciliation_required=1 WHERE effect_ref=? AND provider_state='in_flight'",
            (effect_ref,),
        ).rowcount
        if changed != 1:
            raise ValueError("in-flight effect required")
        self._db.execute(
            "INSERT OR REPLACE INTO become_effect_reconciliation VALUES (?, 'required', NULL, ?)",
            (effect_ref, _iso(at)),
        )

    def retry(self, effect_ref: str) -> None:
        row = self._db.execute(
            "SELECT provider_state FROM become_controlled_effect WHERE effect_ref=?", (effect_ref,)
        ).fetchone()
        if not row:
            raise KeyError(effect_ref)
        if row["provider_state"] in {"in_flight", "settled", "unknown"}:
            raise BlindRetryForbidden(f"cannot blindly retry {row['provider_state']} effect")

    def reconcile(
        self, effect_ref: str, receipt_ref: str, no_effect_proven: bool, at: datetime
    ) -> None:
        _opaque(receipt_ref, "reconciliation_receipt_")
        row = self._db.execute(
            "SELECT provider_state FROM become_controlled_effect WHERE effect_ref=?", (effect_ref,)
        ).fetchone()
        if not row or row["provider_state"] != "unknown":
            raise ValueError("unknown effect required")
        next_state = "reserved" if no_effect_proven else "settled"
        self._db.execute(
            "UPDATE become_controlled_effect SET provider_state=?, reconciliation_required=0 WHERE effect_ref=?",
            (next_state, effect_ref),
        )
        self._db.execute(
            "UPDATE become_effect_reconciliation SET status='resolved', receipt_ref=?, updated_at=? WHERE effect_ref=?",
            (receipt_ref, _iso(at), effect_ref),
        )

    def counts(self) -> dict[str, int]:
        return {
            "effects": self._db.execute("SELECT count(*) FROM become_controlled_effect").fetchone()[
                0
            ],
            "blockers": self._db.execute("SELECT count(*) FROM become_effect_blocker").fetchone()[
                0
            ],
            "reconciliation": self._db.execute(
                "SELECT count(*) FROM become_effect_reconciliation"
            ).fetchone()[0],
        }
