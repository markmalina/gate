"""Test-only, default-off AQUB v2 crash-rehearsal harness.

This module has no API, worker, settings, or runtime registration.  It uses a
caller-supplied SQLite file only to prove process-loss semantics in synthetic
tests; the production-shaped PostgreSQL ledger remains the runtime contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn

from app.application.aqub_contract_v2 import (
    assert_delivery_correlation_v2,
    parse_contract_json_v2,
    parse_delivery_command_v2,
    parse_delivery_receipt_v2,
)

JsonObject = dict[str, Any]
_SIGNATURE_RE = re.compile(r"^sha256=([0-9a-f]{64})$")
_MINIMUM_SECRET_BYTES = 32


class SyntheticGateMode(StrEnum):
    DISABLED = "disabled"
    REHEARSAL = "synthetic_rehearsal"


class SyntheticGateError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SyntheticCrash(BaseException):
    """Deliberate process-loss boundary; never converted to a delivery fact."""


class SyntheticProviderRejected(RuntimeError):
    pass


class SyntheticProviderUnknown(RuntimeError):
    pass


def _now_iso(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _opaque(kind: str, token: str) -> str:
    return f"{kind}:a{hashlib.sha256(token.encode()).hexdigest()}"


def _ack(command: Mapping[str, Any], status: str, observed_at: str, reason: str | None = None):
    value: JsonObject = {
        "schema_version": "aqub.delivery-ack.v2",
        "ack_ref": _opaque("ack", f"{command['command_id']}:{status}"),
        "command_id": command["command_id"],
        "idempotency_key": command["idempotency_key"],
        "installation_ref": command["installation_ref"],
        "cube_ref": command["cube_ref"],
        "route_generation": command["route_generation"],
        "observed_at": observed_at,
        "status": status,
    }
    if reason is not None:
        value["reason_code"] = reason
    return value


def _receipt(
    command: Mapping[str, Any],
    status: str,
    observed_at: str,
    *,
    failure_code: str | None = None,
):
    token = f"{command['command_id']}:{status}"
    value: JsonObject = {
        "schema_version": "aqub.delivery-receipt.v2",
        "receipt_ref": _opaque("receipt", token),
        "command_id": command["command_id"],
        "idempotency_key": command["idempotency_key"],
        "installation_ref": command["installation_ref"],
        "cube_ref": command["cube_ref"],
        "route_generation": command["route_generation"],
        "observed_at": observed_at,
        "status": status,
        "retry_disposition": "reconcile_only" if status == "unknown" else "do_not_retry",
        "transport_receipt_ref": _opaque("receipt", f"transport:{token}"),
    }
    if status == "sent":
        value["delivered_message_ref"] = _opaque("message", token)
    elif status == "failed":
        value["failure_code"] = failure_code or "PROVIDER_REJECTED"
    else:
        value["reconciliation_ref"] = _opaque("reconcile", token)
    return parse_delivery_receipt_v2(value).to_wire()


class SyntheticProviderSpy:
    """Persistent at-most-once provider boundary for one rehearsal database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def send(self, command: Mapping[str, Any], outcome: str) -> str:
        key = str(command["idempotency_key"])
        terminal = (
            "sent"
            if outcome in {"sent", "crash_after_acceptance", "crash_after_result"}
            else None
        )
        if outcome == "failed":
            terminal = "failed"
        with self._connection() as connection:
            try:
                connection.execute(
                    """INSERT INTO synthetic_provider_calls(
                         idempotency_key,call_count,outcome,terminal_status
                       ) VALUES(?,1,?,?)""",
                    (key, outcome, terminal),
                )
            except sqlite3.IntegrityError as exc:
                raise SyntheticGateError("GATE_SYNTHETIC_SECOND_PROVIDER_SEND") from exc
        if outcome == "failed":
            raise SyntheticProviderRejected
        if outcome == "unknown":
            raise SyntheticProviderUnknown
        if outcome == "crash_after_acceptance":
            raise SyntheticCrash
        return _opaque("message", f"provider:{key}")

    def resolve(self, idempotency_key: str, status: str) -> None:
        if status not in {"sent", "failed"}:
            raise ValueError("terminal provider status required")
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE synthetic_provider_calls SET terminal_status=? WHERE idempotency_key=?",
                (status, idempotency_key),
            ).rowcount
        if changed != 1:
            raise SyntheticGateError("GATE_SYNTHETIC_PROVIDER_CALL_NOT_FOUND")

    def terminal_status(self, idempotency_key: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT terminal_status FROM synthetic_provider_calls WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else row["terminal_status"]

    def call_count(self, idempotency_key: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT call_count FROM synthetic_provider_calls WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return 0 if row is None else int(row["call_count"])


class SyntheticGateRehearsal:
    def __init__(
        self,
        *,
        database_path: Path,
        secret: str = "",
        mode: SyntheticGateMode = SyntheticGateMode.DISABLED,
        route_active: Callable[[Mapping[str, Any]], bool] = lambda _command: True,
        checkpoint: Callable[[str, Mapping[str, Any]], None] = lambda _name, _value: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._database_path = database_path
        self._secret = secret.encode()
        self._mode = mode
        self._route_active = route_active
        self._checkpoint = checkpoint
        self._clock = clock
        self.provider = SyntheticProviderSpy(database_path)
        if mode is SyntheticGateMode.REHEARSAL:
            self._initialize()

    def _require_enabled(self) -> None:
        if self._mode is not SyntheticGateMode.REHEARSAL:
            raise SyntheticGateError("GATE_SYNTHETIC_REHEARSAL_DISABLED")
        if len(self._secret) < _MINIMUM_SECRET_BYTES:
            raise SyntheticGateError("GATE_SYNTHETIC_HMAC_INVALID")

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS synthetic_delivery_ledger(
                  command_id TEXT PRIMARY KEY,
                  installation_ref TEXT NOT NULL,
                  idempotency_key TEXT NOT NULL,
                  command_sha256 TEXT NOT NULL,
                  raw_body BLOB,
                  command_json TEXT,
                  state TEXT NOT NULL,
                  ack_json TEXT,
                  receipt_json TEXT,
                  provider_outcome TEXT NOT NULL,
                  redacted INTEGER NOT NULL DEFAULT 0,
                  UNIQUE(installation_ref,idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS synthetic_provider_calls(
                  idempotency_key TEXT PRIMARY KEY,
                  call_count INTEGER NOT NULL CHECK(call_count=1),
                  outcome TEXT NOT NULL,
                  terminal_status TEXT
                );
                """
            )

    def accept_raw(self, raw_body: bytes, signature: str, *, provider_outcome: str = "sent"):
        self._require_enabled()
        match = _SIGNATURE_RE.fullmatch(signature)
        expected = hmac.new(self._secret, raw_body, hashlib.sha256).hexdigest()
        if match is None or not hmac.compare_digest(match.group(1), expected):
            raise SyntheticGateError("GATE_SYNTHETIC_SIGNATURE_INVALID")
        document = parse_contract_json_v2(raw_body)
        command = parse_delivery_command_v2(document.to_wire()).to_wire()
        if command["payload"]["kind"] != "text":
            raise SyntheticGateError("GATE_SYNTHETIC_TEXT_ONLY")
        digest = hashlib.sha256(raw_body).hexdigest()
        observed = _now_iso(self._clock)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM synthetic_delivery_ledger
                     WHERE installation_ref=? AND idempotency_key=?""",
                (command["installation_ref"], command["idempotency_key"]),
            ).fetchone()
            if row is None:
                ack = _ack(command, "committed", observed)
                connection.execute(
                    """INSERT INTO synthetic_delivery_ledger(
                         command_id,installation_ref,idempotency_key,command_sha256,raw_body,
                         command_json,state,ack_json,provider_outcome
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        command["command_id"], command["installation_ref"],
                        command["idempotency_key"], digest, raw_body,
                        json.dumps(command, sort_keys=True, separators=(",", ":")),
                        "committed", json.dumps(ack), provider_outcome,
                    ),
                )
                return {"ack": ack, "receipt": None}
            stored = json.loads(row["command_json"]) if row["command_json"] else command
            if row["redacted"]:
                return {
                    "ack": _ack(command, "rejected", observed, "AQUB_V2_CONTENT_REDACTED"),
                    "receipt": None,
                }
            if row["command_sha256"] != digest or stored["command_id"] != command["command_id"]:
                return {
                    "ack": _ack(command, "rejected", observed, "AQUB_V2_IDEMPOTENCY_CONFLICT"),
                    "receipt": None,
                }
            return {
                "ack": _ack(command, "duplicate", observed),
                "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
            }

    def dispatch(self, command_id: str):
        self._require_enabled()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM synthetic_delivery_ledger WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None:
                raise SyntheticGateError("GATE_SYNTHETIC_COMMAND_NOT_FOUND")
            if row["redacted"]:
                raise SyntheticGateError("GATE_SYNTHETIC_CONTENT_REDACTED")
            if row["state"] != "committed":
                return {
                    "kind": "not_dispatchable",
                    "state": row["state"],
                    "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
                }
            command = json.loads(row["command_json"])
            if not self._route_active(command):
                receipt = _receipt(
                    command,
                    "failed",
                    _now_iso(self._clock),
                    failure_code="AQUB_V2_ROUTE_NOT_ACTIVE",
                )
                connection.execute(
                    """UPDATE synthetic_delivery_ledger
                          SET state='failed',receipt_json=? WHERE command_id=?""",
                    (json.dumps(receipt), command_id),
                )
                return {"kind": "failed", "receipt": receipt}
            connection.execute(
                "UPDATE synthetic_delivery_ledger SET state='sending' WHERE command_id=?",
                (command_id,),
            )
            outcome = str(row["provider_outcome"])
        self._checkpoint("before_provider", command)
        try:
            self.provider.send(command, outcome)
            status = "sent"
            failure_code = None
        except SyntheticProviderRejected:
            status = "failed"
            failure_code = "PROVIDER_REJECTED"
        except SyntheticProviderUnknown:
            status = "unknown"
            failure_code = None
        self._checkpoint("after_provider_result", command)
        receipt = _receipt(command, status, _now_iso(self._clock), failure_code=failure_code)
        with self._connection() as connection:
            changed = connection.execute(
                """UPDATE synthetic_delivery_ledger SET state=?,receipt_json=?
                     WHERE command_id=? AND state='sending'""",
                (status, json.dumps(receipt), command_id),
            ).rowcount
        if changed != 1:
            raise SyntheticGateError("GATE_SYNTHETIC_SETTLE_FENCE")
        return {"kind": status, "receipt": receipt}

    def recover_sending(self, command_id: str):
        self._require_enabled()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM synthetic_delivery_ledger WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None or row["state"] != "sending":
                return {"kind": "unchanged"}
            command = json.loads(row["command_json"])
            receipt = _receipt(command, "unknown", _now_iso(self._clock))
            connection.execute(
                """UPDATE synthetic_delivery_ledger
                      SET state='unknown',receipt_json=? WHERE command_id=?""",
                (json.dumps(receipt), command_id),
            )
        return {"kind": "unknown", "receipt": receipt}

    def reconcile(self, command_id: str):
        self._require_enabled()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM synthetic_delivery_ledger WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None or row["state"] != "unknown" or row["redacted"]:
                raise SyntheticGateError("GATE_SYNTHETIC_UNKNOWN_REQUIRED")
            command = json.loads(row["command_json"])
            terminal = self.provider.terminal_status(str(command["idempotency_key"]))
            if terminal is None:
                return {"kind": "unknown", "receipt": json.loads(row["receipt_json"])}
            receipt = _receipt(command, terminal, _now_iso(self._clock))
            assert_delivery_correlation_v2(
                parse_delivery_command_v2(command), parse_delivery_receipt_v2(receipt)
            )
            connection.execute(
                "UPDATE synthetic_delivery_ledger SET state=?,receipt_json=? WHERE command_id=?",
                (terminal, json.dumps(receipt), command_id),
            )
        return {"kind": "reconciled", "receipt": receipt}

    def cancel(self, command_id: str, reason_code: str = "AQUB_V2_CONSENT_REVOKED"):
        """Stop a synthetic command without crossing or repeating the provider boundary."""
        self._require_enabled()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM synthetic_delivery_ledger WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None or row["redacted"]:
                raise SyntheticGateError("GATE_SYNTHETIC_COMMAND_NOT_FOUND")
            if row["receipt_json"]:
                return {"kind": row["state"], "receipt": json.loads(row["receipt_json"])}
            command = json.loads(row["command_json"])
            status = "unknown" if row["state"] == "sending" else "failed"
            receipt = _receipt(
                command,
                status,
                _now_iso(self._clock),
                failure_code=reason_code if status == "failed" else None,
            )
            connection.execute(
                "UPDATE synthetic_delivery_ledger SET state=?,receipt_json=? WHERE command_id=?",
                (status, json.dumps(receipt), command_id),
            )
        return {"kind": status, "receipt": receipt}

    def redact(self, command_id: str) -> None:
        self._require_enabled()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state FROM synthetic_delivery_ledger WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None or row["state"] not in {"sent", "failed", "unknown", "rejected"}:
                raise SyntheticGateError("GATE_SYNTHETIC_REDACTION_NOT_SAFE")
            connection.execute(
                """UPDATE synthetic_delivery_ledger
                      SET state='redacted',raw_body=NULL,command_json=NULL,ack_json=NULL,
                          receipt_json=NULL,redacted=1 WHERE command_id=?""",
                (command_id,),
            )

    def snapshot(self, command_id: str) -> JsonObject:
        self._require_enabled()
        with self._connection() as connection:
            row = connection.execute(
                """SELECT state,idempotency_key,receipt_json,redacted
                     FROM synthetic_delivery_ledger WHERE command_id=?""",
                (command_id,),
            ).fetchone()
        if row is None:
            raise SyntheticGateError("GATE_SYNTHETIC_COMMAND_NOT_FOUND")
        return {
            "state": row["state"],
            "idempotency_key": row["idempotency_key"],
            "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
            "redacted": bool(row["redacted"]),
            "provider_spy_calls": self.provider.call_count(str(row["idempotency_key"])),
        }


def unreachable(value: object) -> NoReturn:
    raise AssertionError(f"unreachable synthetic value: {value!r}")
