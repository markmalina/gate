"""SIMULATED-only Gate entry adapters for the frozen BECOME W2 contract.

No provider client or download implementation exists here.  The module accepts
signed synthetic webhook fixtures, creates stable opaque entry intents and
records consent preflight receipts before a caller may claim a download intent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


FROZEN_DIGEST = "7afe6218f2d81c3c603541154b36f46a550db25377f6cc9a94697823ca597189"
PROVIDER_STATES = frozenset({"reserved", "in_flight", "settled", "unknown"})
CHANNELS = frozenset({"become_bot", "profile_link", "instagram_private_reply"})
FORBIDDEN_KEYS = frozenset(
    {
        "provider_id",
        "provider_user_id",
        "username",
        "email",
        "phone",
        "person_id",
        "transcript",
        "transcript_text",
        "signed_url",
        "download_url",
        "media",
        "media_bytes",
        "voice_bytes",
    }
)
SIGNATURE_CONTEXT = b"synthetic-provider-webhook-v1\n"


class ReplayMutation(ValueError):
    pass


class ConsentRequired(PermissionError):
    pass


class BlindRetryForbidden(RuntimeError):
    pass


class RelationsPreflight(Protocol):
    def eligibility(self, consent_receipt_ref: str, purpose: str) -> str: ...


class FakeRelations:
    """Version-pinned synthetic consent decisions; no identity data."""

    def __init__(self, decisions: dict[str, str]):
        self._decisions = dict(decisions)

    def eligibility(self, consent_receipt_ref: str, purpose: str) -> str:
        if purpose != "voice_media_download":
            return "DENIED"
        result = self._decisions.get(consent_receipt_ref, "UNKNOWN")
        if result not in {"ALLOWED", "DENIED", "UNKNOWN"}:
            raise ValueError("invalid FakeRelations decision")
        return result


@dataclass(frozen=True)
class EntryReceipt:
    webhook_ref: str
    intent_ref: str
    created: bool
    provider_state: str


@dataclass(frozen=True)
class ConsentPreflightReceipt:
    request_ref: str
    consent_receipt_ref: str
    decision: str
    download_permitted: bool


def _canonical(value: Any) -> bytes:
    _reject_sensitive(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reject_sensitive(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"provider/media field forbidden: {sorted(forbidden)[0]}")
        for child in value.values():
            _reject_sensitive(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive(child)


def sign_synthetic_webhook(payload: dict[str, Any]) -> str:
    """Return a fixture signature; this is intentionally not a provider secret."""
    return hashlib.sha256(SIGNATURE_CONTEXT + _canonical(payload)).hexdigest()


def verify_synthetic_webhook(payload: dict[str, Any], signature: str) -> bool:
    expected = sign_synthetic_webhook(payload)
    return len(signature) == 64 and expected == signature


def _utc_iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _opaque(value: str, prefix: str) -> None:
    if not value.startswith(prefix) or len(value) > 160:
        raise ValueError(f"opaque {prefix} ref required")


class GateEntryAdapter:
    def __init__(self, database: str | Path, relations: RelationsPreflight):
        self._relations = relations
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS become_bot_binding (
              binding_ref TEXT PRIMARY KEY,
              bot_ref TEXT NOT NULL,
              profile_link_ref TEXT NOT NULL,
              entry_subject_ref TEXT NOT NULL UNIQUE,
              frozen_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_entry_intent (
              entry_subject_ref TEXT PRIMARY KEY,
              intent_ref TEXT NOT NULL UNIQUE,
              binding_ref TEXT NOT NULL REFERENCES become_bot_binding(binding_ref),
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_provider_webhook (
              webhook_ref TEXT PRIMARY KEY,
              payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
              channel TEXT NOT NULL CHECK(channel IN ('become_bot','profile_link','instagram_private_reply')),
              intent_ref TEXT NOT NULL REFERENCES become_entry_intent(intent_ref),
              provider_state TEXT NOT NULL CHECK(provider_state IN ('reserved','in_flight','settled','unknown')),
              reconciliation_required INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_required IN (0,1)),
              received_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_consent_preflight (
              request_ref TEXT PRIMARY KEY,
              intent_ref TEXT NOT NULL REFERENCES become_entry_intent(intent_ref),
              consent_receipt_ref TEXT NOT NULL,
              decision TEXT NOT NULL CHECK(decision IN ('ALLOWED','DENIED','UNKNOWN')),
              download_permitted INTEGER NOT NULL CHECK(download_permitted IN (0,1)),
              created_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    def bind_bot(
        self,
        binding_ref: str,
        bot_ref: str,
        profile_link_ref: str,
        entry_subject_ref: str,
    ) -> None:
        for value, prefix in (
            (binding_ref, "binding_"),
            (bot_ref, "bot_"),
            (profile_link_ref, "profile_link_"),
            (entry_subject_ref, "entry_subject_"),
        ):
            _opaque(value, prefix)
        existing = self._db.execute(
            "SELECT bot_ref, profile_link_ref, entry_subject_ref FROM become_bot_binding WHERE binding_ref=?",
            (binding_ref,),
        ).fetchone()
        proposed = (bot_ref, profile_link_ref, entry_subject_ref)
        if existing:
            if tuple(existing) != proposed:
                raise ReplayMutation("mutated bot binding rejected")
            return
        self._db.execute(
            "INSERT INTO become_bot_binding VALUES (?, ?, ?, ?, ?)",
            (binding_ref, bot_ref, profile_link_ref, entry_subject_ref, FROZEN_DIGEST),
        )

    def ingest_signed_entry(
        self,
        webhook_ref: str,
        channel: str,
        binding_ref: str,
        payload: dict[str, Any],
        signature: str,
        at: datetime | None = None,
    ) -> EntryReceipt:
        _opaque(webhook_ref, "webhook_")
        if channel not in CHANNELS or payload.get("mode") != "SIMULATED":
            raise ValueError("supported SIMULATED entry channel required")
        if not verify_synthetic_webhook(payload, signature):
            raise ValueError("invalid synthetic webhook signature")
        binding = self._db.execute(
            "SELECT entry_subject_ref FROM become_bot_binding WHERE binding_ref=?", (binding_ref,)
        ).fetchone()
        if not binding:
            raise KeyError(binding_ref)
        if payload.get("entry_subject_ref") != binding["entry_subject_ref"]:
            raise ValueError("webhook subject does not match binding")
        payload_sha = hashlib.sha256(_canonical(payload)).hexdigest()
        intent_ref = "intent_" + hashlib.sha256(
            (FROZEN_DIGEST + binding["entry_subject_ref"]).encode()
        ).hexdigest()[:32]
        stamp = _utc_iso(at)

        self._db.execute("BEGIN IMMEDIATE")
        try:
            existing_webhook = self._db.execute(
                "SELECT payload_sha256, intent_ref, provider_state FROM become_provider_webhook WHERE webhook_ref=?",
                (webhook_ref,),
            ).fetchone()
            if existing_webhook:
                if existing_webhook["payload_sha256"] != payload_sha:
                    raise ReplayMutation("mutated signed webhook replay rejected")
                self._db.execute("COMMIT")
                return EntryReceipt(webhook_ref, existing_webhook["intent_ref"], False, existing_webhook["provider_state"])
            existing_intent = self._db.execute(
                "SELECT intent_ref FROM become_entry_intent WHERE entry_subject_ref=?",
                (binding["entry_subject_ref"],),
            ).fetchone()
            created = existing_intent is None
            if created:
                self._db.execute(
                    "INSERT INTO become_entry_intent VALUES (?, ?, ?, ?)",
                    (binding["entry_subject_ref"], intent_ref, binding_ref, stamp),
                )
            else:
                intent_ref = existing_intent["intent_ref"]
            self._db.execute(
                "INSERT INTO become_provider_webhook VALUES (?, ?, ?, ?, 'reserved', 0, ?)",
                (webhook_ref, payload_sha, channel, intent_ref, stamp),
            )
            self._db.execute("COMMIT")
            return EntryReceipt(webhook_ref, intent_ref, created, "reserved")
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def reserve_private_reply(self, webhook_ref: str) -> str:
        row = self._db.execute(
            "SELECT intent_ref, channel, provider_state FROM become_provider_webhook WHERE webhook_ref=?",
            (webhook_ref,),
        ).fetchone()
        if not row or row["channel"] != "instagram_private_reply":
            raise ValueError("controlled Instagram entry required")
        if row["provider_state"] == "unknown":
            raise BlindRetryForbidden("unknown private-reply outcome requires reconciliation")
        if row["provider_state"] != "reserved":
            raise ValueError("private reply is not reservable")
        return "reply_claim_" + hashlib.sha256((webhook_ref + row["intent_ref"]).encode()).hexdigest()[:24]

    def record_provider_state(self, webhook_ref: str, state: str) -> None:
        if state not in PROVIDER_STATES:
            raise ValueError("invalid provider state")
        current = self._db.execute(
            "SELECT provider_state FROM become_provider_webhook WHERE webhook_ref=?", (webhook_ref,)
        ).fetchone()
        if not current:
            raise KeyError(webhook_ref)
        if current["provider_state"] == "settled" and state != "settled":
            raise ValueError("settled state is terminal")
        self._db.execute(
            "UPDATE become_provider_webhook SET provider_state=?, reconciliation_required=? WHERE webhook_ref=?",
            (state, int(state == "unknown"), webhook_ref),
        )

    def preflight_voice_media_download(
        self,
        request_ref: str,
        intent_ref: str,
        consent_receipt_ref: str | None,
        at: datetime | None = None,
    ) -> ConsentPreflightReceipt:
        _opaque(request_ref, "download_request_")
        if not consent_receipt_ref:
            raise ConsentRequired("consent receipt required before download claim")
        _opaque(consent_receipt_ref, "consent_receipt_")
        if not self._db.execute("SELECT 1 FROM become_entry_intent WHERE intent_ref=?", (intent_ref,)).fetchone():
            raise KeyError(intent_ref)
        decision = self._relations.eligibility(consent_receipt_ref, "voice_media_download")
        permitted = decision == "ALLOWED"
        stamp = _utc_iso(at)
        existing = self._db.execute(
            "SELECT intent_ref, consent_receipt_ref, decision, download_permitted FROM become_consent_preflight WHERE request_ref=?",
            (request_ref,),
        ).fetchone()
        if existing:
            expected = (intent_ref, consent_receipt_ref, decision, int(permitted))
            if tuple(existing) != expected:
                raise ReplayMutation("mutated consent preflight rejected")
        else:
            self._db.execute(
                "INSERT INTO become_consent_preflight VALUES (?, ?, ?, ?, ?, ?)",
                (request_ref, intent_ref, consent_receipt_ref, decision, int(permitted), stamp),
            )
        return ConsentPreflightReceipt(request_ref, consent_receipt_ref, decision, permitted)

    def claim_download(self, request_ref: str) -> str:
        row = self._db.execute(
            "SELECT intent_ref, decision, download_permitted FROM become_consent_preflight WHERE request_ref=?",
            (request_ref,),
        ).fetchone()
        if not row or row["decision"] != "ALLOWED" or not row["download_permitted"]:
            raise ConsentRequired("download claim fails closed without allowed consent")
        # This is only an opaque local claim; there is intentionally no URL or downloader.
        return "download_claim_" + hashlib.sha256((request_ref + row["intent_ref"]).encode()).hexdigest()[:24]

    def counts(self) -> dict[str, int]:
        return {
            table: self._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "become_bot_binding",
                "become_entry_intent",
                "become_provider_webhook",
                "become_consent_preflight",
            )
        }

