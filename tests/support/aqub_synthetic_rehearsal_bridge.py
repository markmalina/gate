"""JSON bridge for the cross-repository Work I rehearsal.

This process is test-only.  Every invocation reopens the caller-supplied SQLite
ledger so the AQUB runner observes the real Gate crash/restart implementation.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.application.aqub_synthetic_rehearsal import (
    SyntheticCrash,
    SyntheticGateMode,
    SyntheticGateRehearsal,
)

_NOW = datetime(2026, 8, 19, 18, 30, tzinfo=UTC)


def _run(request: dict[str, Any]) -> Any:
    crash_boundary = request.get("crash_boundary")

    def checkpoint(name: str, _command: object) -> None:
        if name == crash_boundary:
            raise SyntheticCrash(name)

    gate = SyntheticGateRehearsal(
        database_path=Path(request["database_path"]),
        secret=str(request["secret"]),
        mode=SyntheticGateMode.REHEARSAL,
        route_active=lambda _command: bool(request.get("route_active", True)),
        checkpoint=checkpoint,
        clock=lambda: _NOW,
    )
    operation = request["operation"]
    if operation == "accept_raw":
        return gate.accept_raw(
            base64.b64decode(request["raw_body_base64"], validate=True),
            str(request["signature"]),
            provider_outcome=str(request.get("provider_outcome", "sent")),
        )
    if operation == "dispatch":
        return gate.dispatch(str(request["command_id"]))
    if operation == "recover_sending":
        return gate.recover_sending(str(request["command_id"]))
    if operation == "reconcile":
        return gate.reconcile(str(request["command_id"]))
    if operation == "provider_resolve":
        gate.provider.resolve(str(request["idempotency_key"]), str(request["status"]))
        return {"kind": "resolved"}
    if operation == "cancel":
        return gate.cancel(str(request["command_id"]), str(request["reason_code"]))
    if operation == "redact":
        gate.redact(str(request["command_id"]))
        return {"kind": "redacted"}
    if operation == "snapshot":
        return gate.snapshot(str(request["command_id"]))
    raise ValueError(f"unsupported operation: {operation}")


def main() -> int:
    request = json.load(sys.stdin)
    try:
        response = {"ok": True, "result": _run(request)}
    except SyntheticCrash as error:
        response = {"ok": False, "crashed": True, "boundary": str(error)}
    except Exception as error:  # the caller needs the exact fail-closed code
        response = {
            "ok": False,
            "error": getattr(error, "code", type(error).__name__),
        }
    json.dump(response, sys.stdout, separators=(",", ":"), sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
