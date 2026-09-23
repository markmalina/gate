"""Redacted, offline personal Telegram enrollment check."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.personal_telegram.preflight import check_local_enrollment


def main() -> None:
    parser = argparse.ArgumentParser(prog="gate-personal-telegram-preflight")
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--receipt-file", required=True, type=Path)
    args = parser.parse_args()
    print(
        check_local_enrollment(
            key_file=args.key_file, receipt_file=args.receipt_file
        ).as_json()
    )


if __name__ == "__main__":
    main()
