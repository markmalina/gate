"""Pre-authorized 1 XTR TEST order ledger for frozen W7.

There is no Telegram client and no synchronous BECOME callback in this module.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FROZEN_DIGEST = "48992e08ea18296f6bb07c15c98c2e1288f0bde0ca3d1b088380419dbcdb0213"
PROVIDER_STATES = frozenset({"reserved", "in_flight", "settled", "unknown"})


class ReplayMutation(ValueError):
    pass


class PreCheckoutUnavailable(PermissionError):
    pass


class BlindRetryForbidden(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderClaim:
    order_ref: str
    idempotency_key: str
    provider_state: str
    order_receipt_sha256: str
    created: bool


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _opaque(value: str, prefix: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 180:
        raise ValueError(f"opaque {prefix} ref required")


class StarsTestOrderStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS become_star_test_order (
              order_ref TEXT PRIMARY KEY,
              idempotency_key TEXT NOT NULL UNIQUE,
              payer_ref TEXT NOT NULL,
              result_receipt_ref TEXT NOT NULL,
              currency TEXT NOT NULL CHECK(currency='XTR'),
              amount INTEGER NOT NULL CHECK(amount=1),
              provider_mode TEXT NOT NULL CHECK(provider_mode='TEST'),
              preauthorized INTEGER NOT NULL CHECK(preauthorized=1),
              precheckout_state TEXT NOT NULL CHECK(precheckout_state IN ('not_requested','waiting','approved','timed_out','declined')),
              provider_state TEXT NOT NULL CHECK(provider_state IN ('reserved','in_flight','settled','unknown')),
              reconciliation_required INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_required IN (0,1)),
              order_receipt_sha256 TEXT NOT NULL CHECK(length(order_receipt_sha256)=64),
              successful_payment_ref TEXT,
              frozen_digest TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_star_precheckout (
              query_ref TEXT PRIMARY KEY,
              order_ref TEXT NOT NULL UNIQUE REFERENCES become_star_test_order(order_ref),
              deadline_at TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('waiting','approved','timed_out','declined')),
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_star_webhook (
              webhook_ref TEXT PRIMARY KEY,
              order_ref TEXT NOT NULL,
              provider_receipt_ref TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_star_refund (
              refund_ref TEXT PRIMARY KEY,
              support_ref TEXT NOT NULL UNIQUE,
              order_ref TEXT NOT NULL UNIQUE REFERENCES become_star_test_order(order_ref),
              provider_state TEXT NOT NULL CHECK(provider_state IN ('reserved','in_flight','settled','unknown')),
              reconciliation_required INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_required IN (0,1)),
              mock_receipt_ref TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_star_reconciliation (
              effect_ref TEXT PRIMARY KEY,
              effect_kind TEXT NOT NULL CHECK(effect_kind IN ('payment','refund')),
              status TEXT NOT NULL CHECK(status IN ('required','resolved')),
              receipt_ref TEXT,
              updated_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    def reserve_order(self, envelope: dict[str, Any], at: datetime) -> OrderClaim:
        required = {
            "order_ref",
            "idempotency_key",
            "payer_ref",
            "result_receipt_ref",
            "currency",
            "amount",
            "provider_mode",
            "preauthorized",
        }
        if set(envelope) != required:
            raise ValueError("exact TEST order envelope required")
        for field, prefix in (
            ("order_ref", "stars_order_"),
            ("idempotency_key", "idempotency_"),
            ("payer_ref", "payer_"),
            ("result_receipt_ref", "result_receipt_"),
        ):
            _opaque(envelope[field], prefix)
        if (
            envelope["currency"] != "XTR"
            or envelope["amount"] != 1
            or envelope["provider_mode"] != "TEST"
            or envelope["preauthorized"] is not True
        ):
            raise ValueError("pre-authorized 1 XTR TEST order required")
        body = {"schema": "gate.stars-test-order@1", "frozen_digest": FROZEN_DIGEST, **envelope}
        receipt_sha = _sha(body)
        existing = self._db.execute(
            """SELECT order_ref, payer_ref, result_receipt_ref, currency, amount,
                      provider_mode, preauthorized, order_receipt_sha256, provider_state
                 FROM become_star_test_order WHERE idempotency_key=?""",
            (envelope["idempotency_key"],),
        ).fetchone()
        expected = (
            envelope["order_ref"],
            envelope["payer_ref"],
            envelope["result_receipt_ref"],
            "XTR",
            1,
            "TEST",
            1,
            receipt_sha,
        )
        if existing:
            if tuple(existing)[:8] != expected:
                raise ReplayMutation("mutated order replay rejected")
            return OrderClaim(
                existing["order_ref"],
                envelope["idempotency_key"],
                existing["provider_state"],
                existing["order_receipt_sha256"],
                False,
            )
        self._db.execute(
            """INSERT INTO become_star_test_order VALUES
               (?, ?, ?, ?, 'XTR', 1, 'TEST', 1, 'not_requested', 'reserved', 0, ?, NULL, ?, ?)""",
            (
                envelope["order_ref"],
                envelope["idempotency_key"],
                envelope["payer_ref"],
                envelope["result_receipt_ref"],
                receipt_sha,
                FROZEN_DIGEST,
                _iso(at),
            ),
        )
        return OrderClaim(
            envelope["order_ref"], envelope["idempotency_key"], "reserved", receipt_sha, True
        )

    def enqueue_precheckout(
        self, order_ref: str, query_ref: str, at: datetime, timeout_seconds: int = 10
    ) -> str:
        _opaque(query_ref, "precheckout_query_")
        if not 1 <= timeout_seconds <= 10:
            raise ValueError("bounded pre-checkout timeout required")
        order = self._db.execute(
            "SELECT provider_state, precheckout_state FROM become_star_test_order WHERE order_ref=?",
            (order_ref,),
        ).fetchone()
        if (
            not order
            or order["provider_state"] != "reserved"
            or order["precheckout_state"] != "not_requested"
        ):
            raise PreCheckoutUnavailable("reserved pre-authorized order required")
        deadline = _iso(_utc(at) + timedelta(seconds=timeout_seconds))
        self._db.execute(
            "INSERT INTO become_star_precheckout VALUES (?, ?, ?, 'waiting', ?)",
            (query_ref, order_ref, deadline, _iso(at)),
        )
        self._db.execute(
            "UPDATE become_star_test_order SET precheckout_state='waiting' WHERE order_ref=?",
            (order_ref,),
        )
        return deadline

    def resolve_precheckout(self, query_ref: str, approved: bool, at: datetime) -> str:
        row = self._db.execute(
            "SELECT * FROM become_star_precheckout WHERE query_ref=?", (query_ref,)
        ).fetchone()
        if not row or row["state"] != "waiting":
            raise PreCheckoutUnavailable("waiting pre-checkout required")
        state = (
            "timed_out"
            if row["deadline_at"] <= _iso(at)
            else ("approved" if approved else "declined")
        )
        self._db.execute(
            "UPDATE become_star_precheckout SET state=?, updated_at=? WHERE query_ref=?",
            (state, _iso(at), query_ref),
        )
        provider_state = "in_flight" if state == "approved" else "reserved"
        self._db.execute(
            "UPDATE become_star_test_order SET precheckout_state=?, provider_state=? WHERE order_ref=?",
            (state, provider_state, row["order_ref"]),
        )
        return state

    def record_successful_payment(
        self, webhook_ref: str, order_ref: str, provider_receipt_ref: str, at: datetime
    ) -> bool:
        _opaque(webhook_ref, "webhook_")
        _opaque(provider_receipt_ref, "successful_payment_test_")
        existing = self._db.execute(
            "SELECT order_ref, provider_receipt_ref FROM become_star_webhook WHERE webhook_ref=?",
            (webhook_ref,),
        ).fetchone()
        if existing:
            if tuple(existing) != (order_ref, provider_receipt_ref):
                raise ReplayMutation("mutated payment webhook rejected")
            return False
        changed = self._db.execute(
            """UPDATE become_star_test_order
                  SET provider_state='settled', successful_payment_ref=?, reconciliation_required=0
                WHERE order_ref=? AND provider_state IN ('in_flight','unknown')""",
            (provider_receipt_ref, order_ref),
        ).rowcount
        if changed != 1:
            raise ValueError("in-flight or reconciled TEST order required")
        self._db.execute(
            "INSERT INTO become_star_webhook VALUES (?, ?, ?, ?)",
            (webhook_ref, order_ref, provider_receipt_ref, _iso(at)),
        )
        return True

    def mark_payment_unknown(self, order_ref: str, at: datetime) -> None:
        changed = self._db.execute(
            "UPDATE become_star_test_order SET provider_state='unknown', reconciliation_required=1 WHERE order_ref=? AND provider_state='in_flight'",
            (order_ref,),
        ).rowcount
        if changed != 1:
            raise ValueError("in-flight payment required")
        self._db.execute(
            "INSERT OR REPLACE INTO become_star_reconciliation VALUES (?, 'payment', 'required', NULL, ?)",
            (order_ref, _iso(at)),
        )

    def reserve_refund(
        self, refund_ref: str, support_ref: str, order_ref: str, at: datetime
    ) -> None:
        _opaque(refund_ref, "refund_")
        _opaque(support_ref, "support_case_")
        order = self._db.execute(
            "SELECT provider_state FROM become_star_test_order WHERE order_ref=?", (order_ref,)
        ).fetchone()
        if not order or order["provider_state"] != "settled":
            raise ValueError("settled TEST payment required")
        self._db.execute(
            "INSERT INTO become_star_refund VALUES (?, ?, ?, 'reserved', 0, NULL, ?)",
            (refund_ref, support_ref, order_ref, _iso(at)),
        )

    def begin_refund_mock(self, refund_ref: str) -> None:
        changed = self._db.execute(
            "UPDATE become_star_refund SET provider_state='in_flight' WHERE refund_ref=? AND provider_state='reserved'",
            (refund_ref,),
        ).rowcount
        if changed != 1:
            raise ValueError("reserved refund required")

    def mark_refund_unknown(self, refund_ref: str, at: datetime) -> None:
        changed = self._db.execute(
            "UPDATE become_star_refund SET provider_state='unknown', reconciliation_required=1 WHERE refund_ref=? AND provider_state='in_flight'",
            (refund_ref,),
        ).rowcount
        if changed != 1:
            raise ValueError("in-flight refund required")
        self._db.execute(
            "INSERT OR REPLACE INTO become_star_reconciliation VALUES (?, 'refund', 'required', NULL, ?)",
            (refund_ref, _iso(at)),
        )

    def retry(self, effect_ref: str, kind: str) -> None:
        table, key = (
            ("become_star_test_order", "order_ref")
            if kind == "payment"
            else ("become_star_refund", "refund_ref")
        )
        row = self._db.execute(
            f"SELECT provider_state FROM {table} WHERE {key}=?", (effect_ref,)
        ).fetchone()
        if not row:
            raise KeyError(effect_ref)
        if row["provider_state"] in {"in_flight", "settled", "unknown"}:
            raise BlindRetryForbidden(f"cannot blindly retry {row['provider_state']} {kind}")

    def reconcile(
        self, effect_ref: str, kind: str, receipt_ref: str, effect_proven: bool, at: datetime
    ) -> None:
        _opaque(receipt_ref, "reconciliation_receipt_")
        table, key = (
            ("become_star_test_order", "order_ref")
            if kind == "payment"
            else ("become_star_refund", "refund_ref")
        )
        row = self._db.execute(
            f"SELECT provider_state FROM {table} WHERE {key}=?", (effect_ref,)
        ).fetchone()
        if not row or row["provider_state"] != "unknown":
            raise ValueError("unknown effect required")
        state = "settled" if effect_proven else "reserved"
        self._db.execute(
            f"UPDATE {table} SET provider_state=?, reconciliation_required=0 WHERE {key}=?",
            (state, effect_ref),
        )
        self._db.execute(
            "UPDATE become_star_reconciliation SET status='resolved', receipt_ref=?, updated_at=? WHERE effect_ref=? AND effect_kind=?",
            (receipt_ref, _iso(at), effect_ref, kind),
        )

    def counts(self) -> dict[str, int]:
        return {
            "orders": self._db.execute("SELECT count(*) FROM become_star_test_order").fetchone()[0],
            "webhooks": self._db.execute("SELECT count(*) FROM become_star_webhook").fetchone()[0],
            "refunds": self._db.execute("SELECT count(*) FROM become_star_refund").fetchone()[0],
            "reconciliation": self._db.execute(
                "SELECT count(*) FROM become_star_reconciliation"
            ).fetchone()[0],
        }
