"""One-shot CLI for read-only Telegram contact synchronization."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from app.contact_sync.desktop_export_reader import (
    TelegramDesktopExportError,
    TelegramDesktopExportReader,
)
from app.contact_sync.gate_client import GatePublishError, GateSnapshotPublisher
from app.contact_sync.models import ContactReader
from app.contact_sync.service import ContactSyncError, ContactSyncService
from app.contact_sync.telethon_reader import (
    TelegramContactReadError,
    authorize_session_to_file,
    create_telethon_reader,
    read_session_file,
)


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    """Secret-backed Telegram credentials for the adapter only."""

    api_id: int
    api_hash: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gate-contact-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser(
        "sync", help="read contacts and publish sanitized snapshots"
    )
    sync.add_argument(
        "--desktop-export",
        type=Path,
        help="read an offline Telegram Desktop result.json instead of a user session",
    )
    authorize = subparsers.add_parser(
        "authorize", help="perform one-time Telegram authorization"
    )
    authorize.add_argument("--session-file", required=True, type=Path)
    return parser


async def _run_sync(desktop_export: Path | None) -> int:
    account_alias = _required_env("GATE_CONTACT_SYNC_ACCOUNT_ALIAS")
    gate_url = _required_env("GATE_CONTACT_SYNC_URL")
    gate_token = _required_env("GATE_CONTACT_SYNC_BEARER_TOKEN")
    reader: ContactReader
    if desktop_export is not None:
        reader = TelegramDesktopExportReader(desktop_export)
    else:
        credentials = _telegram_credentials()
        reader = create_telethon_reader(
            api_id=credentials.api_id,
            api_hash=credentials.api_hash,
            session_string=_session_string(),
        )
    publisher = GateSnapshotPublisher(base_url=gate_url, bearer_token=gate_token)
    result = await ContactSyncService(reader, publisher).execute(
        account_alias=account_alias
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


async def _run_authorize(session_file: Path) -> int:
    credentials = _telegram_credentials()
    phone = _required_env("TELEGRAM_CONTACT_PHONE")
    await authorize_session_to_file(
        api_id=credentials.api_id,
        api_hash=credentials.api_hash,
        phone=phone,
        session_file=session_file,
    )
    print(f"Telegram session written to {session_file}; move it to approved secret storage")
    return 0


def _telegram_credentials() -> TelegramCredentials:
    raw_api_id = _required_env("TELEGRAM_CONTACT_API_ID")
    try:
        api_id = int(raw_api_id)
    except ValueError as exc:
        raise TelegramContactReadError("TELEGRAM_CONTACT_API_ID must be an integer") from exc
    if api_id <= 0:
        raise TelegramContactReadError("TELEGRAM_CONTACT_API_ID must be positive")
    return TelegramCredentials(
        api_id=api_id,
        api_hash=_required_env("TELEGRAM_CONTACT_API_HASH"),
    )


def _session_string() -> str:
    direct = os.environ.get("TELEGRAM_CONTACT_SESSION_STRING", "").strip()
    file_value = os.environ.get("TELEGRAM_CONTACT_SESSION_FILE", "").strip()
    if bool(direct) == bool(file_value):
        raise TelegramContactReadError(
            "Set exactly one Telegram contact session source"
        )
    return direct or read_session_file(Path(file_value))


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise TelegramContactReadError(f"Required environment variable is missing: {name}")
    return value


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "authorize":
        return await _run_authorize(args.session_file)
    return await _run_sync(args.desktop_export)


def main() -> None:
    try:
        exit_code = asyncio.run(async_main())
    except (
        ContactSyncError,
        GatePublishError,
        TelegramContactReadError,
        TelegramDesktopExportError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Contact synchronization failed", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
