"""Console-only protected enrollment for a personal Telegram session."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from app.personal_telegram.enrollment import (
    EncryptedSessionFileStore,
    PersonalTelegramEnrollmentError,
    TelethonInteractiveSessionAuthorizer,
    enroll_personal_telegram_session,
    generate_encryption_key_file,
    read_encryption_key_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gate-personal-telegram-enroll")
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser(
        "init-key", help="create a local owner-only encryption key file"
    )
    initialize.add_argument("--key-file", required=True, type=Path)
    enroll = subparsers.add_parser(
        "enroll", help="authorize one account and persist only encrypted session material"
    )
    enroll.add_argument("--key-file", required=True, type=Path)
    enroll.add_argument("--output-directory", required=True, type=Path)
    enroll.add_argument("--account-label", required=True)
    enroll.add_argument("--application-label", required=True)
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-key":
        generate_encryption_key_file(args.key_file)
        print("Enrollment key created in the selected owner-only file.")
        return 0

    raw_api_id = getpass.getpass("Telegram api_id: ").strip()
    try:
        api_id = int(raw_api_id)
    except ValueError as exc:
        raise PersonalTelegramEnrollmentError(
            "personal_telegram_api_id_invalid"
        ) from exc
    api_hash = getpass.getpass("Telegram api_hash: ").strip()
    phone = getpass.getpass("Owner Telegram phone: ").strip()
    authorizer = TelethonInteractiveSessionAuthorizer(
        code_prompt=lambda: getpass.getpass("Telegram login code: ").strip(),
        password_prompt=lambda: getpass.getpass("Telegram 2FA password: "),
    )
    store = EncryptedSessionFileStore(
        output_directory=args.output_directory,
        encryption_key=read_encryption_key_file(args.key_file),
    )
    artifacts = await enroll_personal_telegram_session(
        authorizer=authorizer,
        store=store,
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        account_label=args.account_label,
        application_label=args.application_label,
    )
    print(artifacts.receipt.as_json())
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(async_main())
    except PersonalTelegramEnrollmentError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("personal_telegram_enrollment_failed", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
