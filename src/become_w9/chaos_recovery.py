"""Repository-only provider/payment chaos recovery for W9.

The store contains no provider client.  It exists to prove that uncertain
SIMULATED/TEST effects can only be resolved by a typed, digest-bound receipt and
that every state transition is committed atomically with its evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

FROZEN = "eb934dbafad99831091d9170f46da78c33b25ab00011bf6fd49bab8210a0aff6"
RECONCILIATION_SCHEMA = "gate.reconciliation_receipt@9"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class Mutation(ValueError):
    """An idempotency reference was replayed with different evidence."""


class BlindRetry(RuntimeError):
    """A non-reserved effect cannot be retried."""


class EffectKind(StrEnum):
    WEBHOOK = "webhook"
    PAYMENT_TEST = "payment_test"


class ProviderState(StrEnum):
    RESERVED = "reserved"
    IN_FLIGHT = "in_flight"
    SETTLED = "settled"
    UNKNOWN = "unknown"


class ChaosMode(StrEnum):
    SIMULATED = "SIMULATED"
    TEST = "TEST"


class ChaosStatus(StrEnum):
    SUCCESS = "success"
    UNKNOWN = "unknown"
    FAILED = "failed"


class ReconciliationOutcome(StrEnum):
    EFFECT_PROVEN = "EFFECT_PROVEN"
    NO_EFFECT_PROVEN = "NO_EFFECT_PROVEN"


RECONCILIATION_FIELDS = frozenset(
    {
        "schema",
        "frozen_digest",
        "reconciliation_ref",
        "effect_ref",
        "source_event_ref",
        "source_event_digest",
        "evidence_digest",
        "mode",
        "outcome",
        "receipt_digest",
    }
)


def sha(value: Any) -> str:
    """Return the digest of a byte-stable JSON representation."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_ref(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) <= len(prefix):
        raise ValueError(f"{prefix} opaque reference required")
    return value


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase sha256 digest")
    return value


@dataclass(frozen=True)
class ReconciliationReceipt:
    reconciliation_ref: str
    effect_ref: str
    source_event_ref: str
    source_event_digest: str
    evidence_digest: str
    mode: ChaosMode
    outcome: ReconciliationOutcome
    receipt_digest: str

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> ReconciliationReceipt:
        if not isinstance(value, Mapping) or set(value) != RECONCILIATION_FIELDS:
            raise ValueError("exact typed reconciliation receipt required")
        if value["schema"] != RECONCILIATION_SCHEMA:
            raise ValueError("reconciliation schema mismatch")
        if value["frozen_digest"] != FROZEN:
            raise ValueError("reconciliation frozen digest mismatch")

        receipt_digest = _require_digest(value["receipt_digest"], "receipt_digest")
        unsigned = dict(value)
        unsigned.pop("receipt_digest")
        if sha(unsigned) != receipt_digest:
            raise ValueError("reconciliation receipt digest mismatch")

        try:
            mode = ChaosMode(value["mode"])
            outcome = ReconciliationOutcome(value["outcome"])
        except (TypeError, ValueError) as error:
            raise ValueError("bounded reconciliation enum required") from error

        return cls(
            reconciliation_ref=_require_ref(value["reconciliation_ref"], "reconciliation_"),
            effect_ref=_require_ref(value["effect_ref"], "chaos_effect_"),
            source_event_ref=_require_ref(value["source_event_ref"], "event_"),
            source_event_digest=_require_digest(
                value["source_event_digest"], "source_event_digest"
            ),
            evidence_digest=_require_digest(value["evidence_digest"], "evidence_digest"),
            mode=mode,
            outcome=outcome,
            receipt_digest=receipt_digest,
        )


def build_reconciliation_receipt(
    *,
    reconciliation_ref: str,
    effect_ref: str,
    source_event_ref: str,
    source_event_digest: str,
    evidence_digest: str,
    mode: str,
    outcome: str,
) -> dict[str, str]:
    """Build a fixture receipt; :meth:`reconcile` still verifies every field."""

    body = {
        "schema": RECONCILIATION_SCHEMA,
        "frozen_digest": FROZEN,
        "reconciliation_ref": reconciliation_ref,
        "effect_ref": effect_ref,
        "source_event_ref": source_event_ref,
        "source_event_digest": source_event_digest,
        "evidence_digest": evidence_digest,
        "mode": mode,
        "outcome": outcome,
    }
    return {**body, "receipt_digest": sha(body)}


class ChaosStore:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS effect(
              ref TEXT PRIMARY KEY,
              kind TEXT NOT NULL CHECK(kind IN ('webhook','payment_test')),
              state TEXT NOT NULL CHECK(state IN ('reserved','in_flight','settled','unknown')),
              receipt TEXT
            );
            CREATE TABLE IF NOT EXISTS event(
              event_ref TEXT PRIMARY KEY,
              effect_ref TEXT NOT NULL REFERENCES effect(ref),
              payload_sha TEXT NOT NULL CHECK(length(payload_sha) = 64),
              mode TEXT NOT NULL CHECK(mode IN ('SIMULATED','TEST')),
              status TEXT NOT NULL CHECK(status IN ('success','unknown','failed')),
              receipt_ref TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reconcile(
              reconciliation_ref TEXT PRIMARY KEY,
              effect_ref TEXT NOT NULL UNIQUE REFERENCES effect(ref),
              source_event_ref TEXT NOT NULL UNIQUE REFERENCES event(event_ref),
              source_event_digest TEXT NOT NULL CHECK(length(source_event_digest) = 64),
              evidence_digest TEXT NOT NULL CHECK(length(evidence_digest) = 64),
              mode TEXT NOT NULL CHECK(mode IN ('SIMULATED','TEST')),
              outcome TEXT NOT NULL CHECK(outcome IN ('EFFECT_PROVEN','NO_EFFECT_PROVEN')),
              receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64)
            );
            """
        )

    def close(self) -> None:
        self.db.close()

    def reserve(self, ref: str, kind: str) -> None:
        effect_kind = EffectKind(kind)
        _require_ref(ref, "chaos_effect_")
        self.db.execute(
            "INSERT INTO effect VALUES(?,?,?,NULL)",
            (ref, effect_kind.value, ProviderState.RESERVED.value),
        )

    def ingest(self, event_ref: str, effect_ref: str, payload: Mapping[str, object]) -> bool:
        _require_ref(event_ref, "event_")
        _require_ref(effect_ref, "chaos_effect_")
        if not isinstance(payload, Mapping) or set(payload) != {"mode", "status", "receipt_ref"}:
            raise ValueError("exact chaos payload required")
        try:
            mode = ChaosMode(payload["mode"])
            status = ChaosStatus(payload["status"])
        except (TypeError, ValueError) as error:
            raise ValueError("bounded chaos enum required") from error
        receipt_ref = _require_ref(payload["receipt_ref"], "mock_")
        payload_digest = sha(dict(payload))

        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                """
                SELECT effect_ref,payload_sha,mode,status,receipt_ref
                FROM event WHERE event_ref=?
                """,
                (event_ref,),
            ).fetchone()
            expected = (effect_ref, payload_digest, mode.value, status.value, receipt_ref)
            if existing:
                if tuple(existing) != expected:
                    raise Mutation("mutated event replay rejected")
                self.db.execute("COMMIT")
                return False

            effect = self.db.execute(
                "SELECT kind,state FROM effect WHERE ref=?", (effect_ref,)
            ).fetchone()
            if not effect:
                raise ValueError("orphan event rejected")
            if effect["state"] != ProviderState.RESERVED.value:
                raise ValueError("reserved effect required")
            if (effect["kind"], mode.value) not in {
                (EffectKind.WEBHOOK.value, ChaosMode.SIMULATED.value),
                (EffectKind.PAYMENT_TEST.value, ChaosMode.TEST.value),
            }:
                raise ValueError("effect kind and mode mismatch")

            next_state = {
                ChaosStatus.SUCCESS: ProviderState.SETTLED,
                ChaosStatus.UNKNOWN: ProviderState.UNKNOWN,
                ChaosStatus.FAILED: ProviderState.RESERVED,
            }[status]
            self.db.execute(
                "UPDATE effect SET state=?,receipt=? WHERE ref=?",
                (next_state.value, receipt_ref, effect_ref),
            )
            self.db.execute(
                "INSERT INTO event VALUES(?,?,?,?,?,?)",
                (event_ref, effect_ref, payload_digest, mode.value, status.value, receipt_ref),
            )
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def retry(self, ref: str) -> None:
        row = self.db.execute("SELECT state FROM effect WHERE ref=?", (ref,)).fetchone()
        if not row:
            raise ValueError("effect required")
        if row["state"] != ProviderState.RESERVED.value:
            raise BlindRetry(row["state"])

    def reconcile(self, value: Mapping[str, object]) -> bool:
        """Resolve UNKNOWN atomically from exact typed evidence; never from a boolean."""

        receipt = ReconciliationReceipt.parse(value)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                """
                SELECT effect_ref,source_event_ref,source_event_digest,evidence_digest,
                       mode,outcome,receipt_digest
                FROM reconcile WHERE reconciliation_ref=?
                """,
                (receipt.reconciliation_ref,),
            ).fetchone()
            expected = (
                receipt.effect_ref,
                receipt.source_event_ref,
                receipt.source_event_digest,
                receipt.evidence_digest,
                receipt.mode.value,
                receipt.outcome.value,
                receipt.receipt_digest,
            )
            if existing:
                if tuple(existing) != expected:
                    raise Mutation("mutated reconciliation replay rejected")
                self.db.execute("COMMIT")
                return False

            duplicate = self.db.execute(
                "SELECT reconciliation_ref FROM reconcile WHERE effect_ref=?",
                (receipt.effect_ref,),
            ).fetchone()
            if duplicate:
                raise Mutation("effect already reconciled by another receipt")

            effect = self.db.execute(
                "SELECT state FROM effect WHERE ref=?", (receipt.effect_ref,)
            ).fetchone()
            if not effect:
                raise ValueError("orphan reconciliation rejected")
            if effect["state"] != ProviderState.UNKNOWN.value:
                raise ValueError("unknown effect required")

            source = self.db.execute(
                "SELECT effect_ref,payload_sha,mode,status FROM event WHERE event_ref=?",
                (receipt.source_event_ref,),
            ).fetchone()
            if not source:
                raise ValueError("orphan reconciliation event rejected")
            if tuple(source) != (
                receipt.effect_ref,
                receipt.source_event_digest,
                receipt.mode.value,
                ChaosStatus.UNKNOWN.value,
            ):
                raise ValueError("reconciliation source evidence mismatch")

            next_state = {
                ReconciliationOutcome.EFFECT_PROVEN: ProviderState.SETTLED,
                ReconciliationOutcome.NO_EFFECT_PROVEN: ProviderState.RESERVED,
            }[receipt.outcome]
            self.db.execute(
                "UPDATE effect SET state=? WHERE ref=?",
                (next_state.value, receipt.effect_ref),
            )
            self.db.execute(
                "INSERT INTO reconcile VALUES(?,?,?,?,?,?,?,?)",
                (
                    receipt.reconciliation_ref,
                    receipt.effect_ref,
                    receipt.source_event_ref,
                    receipt.source_event_digest,
                    receipt.evidence_digest,
                    receipt.mode.value,
                    receipt.outcome.value,
                    receipt.receipt_digest,
                ),
            )
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def deploy_parity(
        self,
        test_config: Mapping[str, object],
        deploy_config: Mapping[str, object],
    ) -> bool:
        return sha(dict(test_config)) == sha(dict(deploy_config))
