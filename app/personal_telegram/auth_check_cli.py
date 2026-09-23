"""Explicit read-only local authorization check; no secrets on argv or stdout."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.personal_telegram.auth_check import _status, check_local_authorization
from app.personal_telegram.local_runtime import LocalEncryptedSessionSource


def main() -> None:
    parser = argparse.ArgumentParser(prog="gate-personal-telegram-check-auth")
    parser.add_argument("--live-readonly", required=True, action="store_true")
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--receipt-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = json.loads(args.receipt_file.read_text(encoding="utf-8"))
        session_ref = receipt["session_ref"]
        api_id = int(os.environ["GATE_PERSONAL_TELEGRAM_API_ID"])
        api_hash = os.environ["GATE_PERSONAL_TELEGRAM_API_HASH"]
        owner_id = int(os.environ["GATE_PERSONAL_TELEGRAM_OWNER_USER_ID"])
        if not isinstance(session_ref, str):
            raise ValueError("invalid_session_ref")
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        print(_status(error_code="local_binding_unavailable").as_json())
        return
    source = LocalEncryptedSessionSource(key_file=args.key_file, receipt_file=args.receipt_file)
    print(
        asyncio.run(
            check_local_authorization(
                source=source,
                session_ref=session_ref,
                api_id=api_id,
                api_hash=api_hash,
                expected_owner_user_id=owner_id,
            )
        ).as_json()
    )
