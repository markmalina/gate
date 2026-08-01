"""SIMULATED referral delivery with exact attribution and provider-free receipts."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FROZEN_DIGEST = "099f8b5bca93a3558ce9fa9b1ec4019304b126810c06b65384e3e71cd09c24e5"
STATUS_MAP = {
    "accepted": ("in_flight", "pending", False),
    "delivered": ("settled", "delivered", False),
    "failed": ("settled", "failed", False),
    "unknown": ("unknown", "pending", True),
}


class ReplayMutation(ValueError):
    pass


class BlindRetryForbidden(RuntimeError):
    pass


def _sha(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _iso(value):
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ref(value, prefix):
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 180:
        raise ValueError(f"opaque {prefix} ref required")


def _digest(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{label} must be lowercase sha256")


@dataclass(frozen=True)
class Claim:
    delivery_ref: str
    effect_state: str
    created: bool


class ReferralDeliveryStore:
    def __init__(self, database: str | Path):
        self.db = sqlite3.connect(str(database), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS delivery(
              delivery_ref TEXT PRIMARY KEY,
              idempotency_key TEXT UNIQUE NOT NULL,
              referral_edge_ref TEXT NOT NULL,
              edge_revision INTEGER NOT NULL,
              referrer_person_ref TEXT NOT NULL,
              candidate_person_ref TEXT NOT NULL,
              attribution_projection_sha256 TEXT NOT NULL,
              link_payload_sha256 TEXT NOT NULL,
              effect_state TEXT NOT NULL,
              delivery_outcome TEXT NOT NULL,
              status TEXT NOT NULL,
              status_revision INTEGER NOT NULL,
              reconciliation_required INTEGER NOT NULL,
              status_receipt_sha256 TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS status_event(
              event_ref TEXT PRIMARY KEY,
              delivery_ref TEXT NOT NULL,
              request_sha256 TEXT NOT NULL,
              receipt_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reconciliation(
              receipt_ref TEXT PRIMARY KEY,
              delivery_ref TEXT UNIQUE NOT NULL,
              request_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

    def close(self):
        self.db.close()

    def reserve(self, event, at):
        required = {"delivery_ref", "idempotency_key", "attribution", "link", "contract_digest", "mode"}
        if set(event) != required or event["mode"] != "SIMULATED" or event["contract_digest"] != FROZEN_DIGEST:
            raise ValueError("exact SIMULATED reserve envelope required")
        _ref(event["delivery_ref"], "referral_delivery_")
        _ref(event["idempotency_key"], "idempotency_")

        attribution = event["attribution"]
        attr_keys = {"schema", "referral_edge_ref", "edge_revision", "edge_state", "referrer_person_ref", "candidate_person_ref", "supersedes_ref", "projection_sha256", "contract_digest", "mode"}
        if set(attribution) != attr_keys or attribution["schema"] != "relations.referral-attribution-projection@1.0.0" or attribution["edge_state"] != "ACTIVE" or attribution["contract_digest"] != FROZEN_DIGEST or attribution["mode"] != "SIMULATED":
            raise ValueError("active frozen attribution required")
        _ref(attribution["referral_edge_ref"], "referral_edge_")
        _ref(attribution["referrer_person_ref"], "person_")
        _ref(attribution["candidate_person_ref"], "person_")
        if not isinstance(attribution["edge_revision"], int) or attribution["edge_revision"] < 1:
            raise ValueError("positive edge revision required")
        _digest(attribution["projection_sha256"], "projection_sha256")

        link = event["link"]
        link_keys = {"schema", "link_ref", "referral_edge_ref", "referrer_person_ref", "candidate_person_ref", "payload_sha256", "expires_at", "signature_receipt_sha256", "contract_digest", "mode"}
        if set(link) != link_keys or link["schema"] != "become.next-cycle-link-fixture@1.0.0" or link["contract_digest"] != FROZEN_DIGEST or link["mode"] != "SIMULATED":
            raise ValueError("exact frozen link fixture required")
        _ref(link["link_ref"], "next_cycle_link_")
        _digest(link["payload_sha256"], "payload_sha256")
        _digest(link["signature_receipt_sha256"], "signature_receipt_sha256")
        if _parse(link["expires_at"]) <= at.astimezone(timezone.utc):
            raise ValueError("unexpired link required")
        tuple_fields = ("referral_edge_ref", "referrer_person_ref", "candidate_person_ref")
        if any(link[field] != attribution[field] for field in tuple_fields):
            raise ValueError("link attribution tuple mismatch")

        immutable = (
            event["delivery_ref"], attribution["referral_edge_ref"], attribution["edge_revision"],
            attribution["referrer_person_ref"], attribution["candidate_person_ref"],
            attribution["projection_sha256"], link["payload_sha256"],
        )
        old = self.db.execute("SELECT * FROM delivery WHERE idempotency_key=?", (event["idempotency_key"],)).fetchone()
        if old:
            prior = tuple(old[key] for key in ("delivery_ref", "referral_edge_ref", "edge_revision", "referrer_person_ref", "candidate_person_ref", "attribution_projection_sha256", "link_payload_sha256"))
            if prior != immutable:
                raise ReplayMutation("mutated delivery reserve")
            return Claim(old["delivery_ref"], old["effect_state"], False)
        self.db.execute(
            "INSERT INTO delivery VALUES(?,?,?,?,?,?,?,?,?,'pending','queued',0,0,NULL,?)",
            (event["delivery_ref"], event["idempotency_key"], attribution["referral_edge_ref"], attribution["edge_revision"], attribution["referrer_person_ref"], attribution["candidate_person_ref"], attribution["projection_sha256"], link["payload_sha256"], "reserved", _iso(at)),
        )
        return Claim(event["delivery_ref"], "reserved", True)

    def observe(self, event_ref, delivery_ref, status, synthetic_provider_ref, at):
        _ref(event_ref, "provider_event_")
        _ref(synthetic_provider_ref, "synthetic_provider_")
        if status not in STATUS_MAP:
            raise ValueError("bounded status required")
        request = {"event_ref": event_ref, "delivery_ref": delivery_ref, "status": status, "synthetic_provider_ref": synthetic_provider_ref, "mode": "SIMULATED"}
        request_sha = _sha(request)
        receipt_sha = _sha({"request_sha256": request_sha, "contract_digest": FROZEN_DIGEST})
        old_event = self.db.execute("SELECT delivery_ref,request_sha256,receipt_sha256 FROM status_event WHERE event_ref=?", (event_ref,)).fetchone()
        if old_event:
            if tuple(old_event) != (delivery_ref, request_sha, receipt_sha):
                raise ReplayMutation("mutated provider event")
            return self.projection(delivery_ref)
        current = self.db.execute("SELECT effect_state,delivery_outcome,status_revision FROM delivery WHERE delivery_ref=?", (delivery_ref,)).fetchone()
        if not current:
            raise KeyError(delivery_ref)
        if current["effect_state"] in {"settled", "unknown"}:
            raise ValueError("terminal or unknown delivery cannot accept status")
        effect_state, outcome, reconciliation = STATUS_MAP[status]
        revision = current["status_revision"] + 1
        self.db.execute(
            "UPDATE delivery SET effect_state=?,delivery_outcome=?,status=?,status_revision=?,reconciliation_required=?,status_receipt_sha256=? WHERE delivery_ref=?",
            (effect_state, outcome, status, revision, int(reconciliation), receipt_sha, delivery_ref),
        )
        self.db.execute("INSERT INTO status_event VALUES(?,?,?,?,?)", (event_ref, delivery_ref, request_sha, receipt_sha, _iso(at)))
        return self.projection(delivery_ref)

    def retry(self, delivery_ref):
        state = self.db.execute("SELECT effect_state FROM delivery WHERE delivery_ref=?", (delivery_ref,)).fetchone()
        if not state:
            raise KeyError(delivery_ref)
        if state[0] != "reserved":
            raise BlindRetryForbidden(state[0])
        return True

    def reconcile(self, delivery_ref, receipt_ref, evidence, at):
        _ref(receipt_ref, "reconciliation_receipt_")
        if evidence not in {"EFFECT_PROVEN", "NO_EFFECT_PROVEN"}:
            raise ValueError("exact reconciliation evidence required")
        current = self.db.execute("SELECT effect_state,status_revision FROM delivery WHERE delivery_ref=?", (delivery_ref,)).fetchone()
        if not current or current["effect_state"] != "unknown":
            raise ValueError("unknown delivery required")
        request = {"delivery_ref": delivery_ref, "receipt_ref": receipt_ref, "evidence": evidence, "contract_digest": FROZEN_DIGEST}
        request_sha = _sha(request)
        old = self.db.execute("SELECT delivery_ref,request_sha256 FROM reconciliation WHERE receipt_ref=?", (receipt_ref,)).fetchone()
        if old:
            if tuple(old) != (delivery_ref, request_sha):
                raise ReplayMutation("mutated reconciliation")
            return self.projection(delivery_ref)
        outcome, status = ("delivered", "delivered") if evidence == "EFFECT_PROVEN" else ("failed", "failed")
        receipt_sha = _sha(request)
        self.db.execute(
            "UPDATE delivery SET effect_state='settled',delivery_outcome=?,status=?,status_revision=?,reconciliation_required=0,status_receipt_sha256=? WHERE delivery_ref=?",
            (outcome, status, current["status_revision"] + 1, receipt_sha, delivery_ref),
        )
        self.db.execute("INSERT INTO reconciliation VALUES(?,?,?,?)", (receipt_ref, delivery_ref, request_sha, _iso(at)))
        return self.projection(delivery_ref)

    def projection(self, delivery_ref):
        row = self.db.execute("SELECT * FROM delivery WHERE delivery_ref=?", (delivery_ref,)).fetchone()
        if not row:
            raise KeyError(delivery_ref)
        return {
            "schema": "gate.referral-delivery-acceptance@1.0.0",
            "delivery_ref": row["delivery_ref"],
            "referral_edge_ref": row["referral_edge_ref"],
            "edge_revision": row["edge_revision"],
            "referrer_person_ref": row["referrer_person_ref"],
            "candidate_person_ref": row["candidate_person_ref"],
            "attribution_projection_sha256": row["attribution_projection_sha256"],
            "link_payload_sha256": row["link_payload_sha256"],
            "effect_state": row["effect_state"],
            "delivery_outcome": row["delivery_outcome"],
            "status": row["status"],
            "status_revision": row["status_revision"],
            "reconciliation_required": bool(row["reconciliation_required"]),
            "status_receipt_sha256": row["status_receipt_sha256"],
            "contract_digest": FROZEN_DIGEST,
            "mode": "SIMULATED",
        }
