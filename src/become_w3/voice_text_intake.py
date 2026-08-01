"""W3 SIMULATED Gate transport intake and asset quarantine.

Gate stores only opaque Telegram file references and text digests.  It has no
download or transcription implementation; released assets become opaque TAKE
requests after consent and quarantine checks.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FROZEN_DIGEST = "af767a200a3dc20396814c1c36fafacd9ae871e4273bf0b04b5fe53056b502f6"
PROVIDER_STATES = frozenset({"reserved", "in_flight", "settled", "unknown"})
FORBIDDEN_KEYS = frozenset(
    {
        "file_bytes",
        "media_bytes",
        "voice_bytes",
        "signed_url",
        "download_url",
        "transcript",
        "transcript_text",
        "provider_user_id",
        "person_id",
        "email",
        "phone",
    }
)


class ReplayMutation(ValueError):
    pass


class QuarantineBlocked(PermissionError):
    pass


class BlindRetryForbidden(RuntimeError):
    pass


@dataclass(frozen=True)
class IntakeReceipt:
    intake_ref: str
    asset_ref: str | None
    content_sha256: str
    created: bool


def _canonical(value: Any) -> bytes:
    _reject_sensitive(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reject_sensitive(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"forbidden transport payload: {sorted(forbidden)[0]}")
        for child in value.values():
            _reject_sensitive(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive(child)


def _iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _opaque(value: str, prefix: str) -> None:
    if not value.startswith(prefix) or len(value) > 180:
        raise ValueError(f"opaque {prefix} ref required")


class VoiceTextIntakeStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS become_w3_intake (
              intake_ref TEXT PRIMARY KEY,
              quest_ref TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('text','voice')),
              payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
              asset_ref TEXT UNIQUE,
              frozen_digest TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS become_w3_asset_quarantine (
              asset_ref TEXT PRIMARY KEY REFERENCES become_w3_intake(asset_ref),
              telegram_file_ref_sha256 TEXT NOT NULL CHECK(length(telegram_file_ref_sha256)=64),
              download_claim_ref TEXT NOT NULL,
              consent_receipt_ref TEXT NOT NULL,
              quarantine_state TEXT NOT NULL CHECK(quarantine_state IN ('pending','released','rejected')),
              provider_state TEXT NOT NULL CHECK(provider_state IN ('reserved','in_flight','settled','unknown')),
              reconciliation_required INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_required IN (0,1)),
              updated_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    def record_text(self, intake_ref: str, quest_ref: str, synthetic_text: str, at: datetime | None = None) -> IntakeReceipt:
        _opaque(intake_ref, "intake_")
        _opaque(quest_ref, "quest_")
        if not synthetic_text.startswith("SYNTHETIC:"):
            raise ValueError("only explicitly synthetic text fixtures are accepted")
        content_sha = hashlib.sha256(synthetic_text.encode()).hexdigest()
        # The text is intentionally not persisted—only its digest.
        return self._record(intake_ref, quest_ref, "text", content_sha, None, at)

    def record_voice(
        self,
        intake_ref: str,
        quest_ref: str,
        telegram_file_ref: str,
        download_claim_ref: str,
        consent_receipt_ref: str,
        at: datetime | None = None,
    ) -> IntakeReceipt:
        for value, prefix in (
            (intake_ref, "intake_"),
            (quest_ref, "quest_"),
            (telegram_file_ref, "telegram_file_"),
            (download_claim_ref, "download_claim_"),
            (consent_receipt_ref, "consent_receipt_"),
        ):
            _opaque(value, prefix)
        content_sha = hashlib.sha256(telegram_file_ref.encode()).hexdigest()
        asset_ref = "asset_" + hashlib.sha256((FROZEN_DIGEST + telegram_file_ref).encode()).hexdigest()[:32]
        receipt = self._record(intake_ref, quest_ref, "voice", content_sha, asset_ref, at)
        stamp = _iso(at)
        existing = self._db.execute(
            "SELECT telegram_file_ref_sha256, download_claim_ref, consent_receipt_ref FROM become_w3_asset_quarantine WHERE asset_ref=?",
            (asset_ref,),
        ).fetchone()
        expected = (content_sha, download_claim_ref, consent_receipt_ref)
        if existing:
            if tuple(existing) != expected:
                raise ReplayMutation("mutated quarantine replay rejected")
        else:
            self._db.execute(
                "INSERT INTO become_w3_asset_quarantine VALUES (?, ?, ?, ?, 'pending', 'reserved', 0, ?)",
                (asset_ref, content_sha, download_claim_ref, consent_receipt_ref, stamp),
            )
        return receipt

    def _record(self, intake_ref: str, quest_ref: str, kind: str, content_sha: str, asset_ref: str | None, at: datetime | None) -> IntakeReceipt:
        existing = self._db.execute(
            "SELECT quest_ref, kind, payload_sha256, asset_ref FROM become_w3_intake WHERE intake_ref=?", (intake_ref,)
        ).fetchone()
        expected = (quest_ref, kind, content_sha, asset_ref)
        if existing:
            if tuple(existing) != expected:
                raise ReplayMutation("mutated intake replay rejected")
            return IntakeReceipt(intake_ref, asset_ref, content_sha, False)
        self._db.execute(
            "INSERT INTO become_w3_intake VALUES (?, ?, ?, ?, ?, ?, ?)",
            (intake_ref, quest_ref, kind, content_sha, asset_ref, FROZEN_DIGEST, _iso(at)),
        )
        return IntakeReceipt(intake_ref, asset_ref, content_sha, True)

    def decide_quarantine(self, asset_ref: str, decision: str, at: datetime | None = None) -> None:
        if decision not in {"released", "rejected"}:
            raise ValueError("explicit quarantine decision required")
        changed = self._db.execute(
            "UPDATE become_w3_asset_quarantine SET quarantine_state=?, updated_at=? WHERE asset_ref=? AND quarantine_state='pending'",
            (decision, _iso(at), asset_ref),
        ).rowcount
        if changed != 1:
            raise ValueError("pending quarantine asset required")

    def make_take_request(self, asset_ref: str) -> dict[str, str]:
        row = self._db.execute(
            "SELECT quarantine_state, provider_state, telegram_file_ref_sha256 FROM become_w3_asset_quarantine WHERE asset_ref=?",
            (asset_ref,),
        ).fetchone()
        if not row or row["quarantine_state"] != "released":
            raise QuarantineBlocked("asset must be explicitly released")
        if row["provider_state"] == "unknown":
            raise BlindRetryForbidden("unknown transport state requires reconciliation")
        if row["provider_state"] != "reserved":
            raise QuarantineBlocked("asset is not reservable")
        return {
            "mode": "SIMULATED",
            "asset_ref": asset_ref,
            "asset_ref_sha256": row["telegram_file_ref_sha256"],
            "transcriber": "TAKE",
            "frozen_digest": FROZEN_DIGEST,
        }

    def record_provider_state(self, asset_ref: str, state: str) -> None:
        if state not in PROVIDER_STATES:
            raise ValueError("invalid provider state")
        current = self._db.execute(
            "SELECT provider_state FROM become_w3_asset_quarantine WHERE asset_ref=?", (asset_ref,)
        ).fetchone()
        if not current:
            raise KeyError(asset_ref)
        if current["provider_state"] == "settled" and state != "settled":
            raise ValueError("settled state is terminal")
        self._db.execute(
            "UPDATE become_w3_asset_quarantine SET provider_state=?, reconciliation_required=? WHERE asset_ref=?",
            (state, int(state == "unknown"), asset_ref),
        )

    def counts(self) -> dict[str, int]:
        return {
            "intakes": self._db.execute("SELECT count(*) FROM become_w3_intake").fetchone()[0],
            "assets": self._db.execute("SELECT count(*) FROM become_w3_asset_quarantine").fetchone()[0],
        }

