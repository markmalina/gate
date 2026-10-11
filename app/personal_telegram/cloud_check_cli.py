"""Redacted cloud inspection and explicitly invoked owner-only auth probe."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping

from app.application.personal_telegram_transport import PersonalTelegramTransportError
from app.personal_telegram.auth_check import check_local_authorization
from app.personal_telegram.cloud_session import (
    CloudEncryptedSessionSource,
    CloudSessionBinding,
    cloud_binding_enabled,
    inspect_cloud_session,
)


async def check_cloud_session(
    environment: Mapping[str, str], *, live_readonly: bool = False
) -> dict[str, object]:
    """No network by default; live mode checks authorization/owner only."""
    status = inspect_cloud_session(environment)
    if not live_readonly or not status["configured"]:
        return status
    if not cloud_binding_enabled(environment):
        status["error_code"] = "cloud_binding_disabled"
        return status
    try:
        binding = CloudSessionBinding.from_environment(environment)
        result = await check_local_authorization(
            source=CloudEncryptedSessionSource(binding),
            session_ref=binding.session_ref,
            api_id=binding.api_id,
            api_hash=binding.api_hash,
            expected_owner_user_id=binding.expected_owner_user_id,
        )
        return dict(json.loads(result.as_json()))
    except PersonalTelegramTransportError:
        status["error_code"] = "cloud_binding_invalid"
        return status


def main() -> None:
    parser = argparse.ArgumentParser(prog="gate-personal-telegram-cloud-check")
    parser.add_argument(
        "--live-readonly", action="store_true",
        help="explicitly check account authorization once; no history, contacts or sends",
    )
    args = parser.parse_args()
    status = asyncio.run(check_cloud_session(os.environ, live_readonly=args.live_readonly))
    print(json.dumps(status, sort_keys=True))
    if status["configured"] is not True or (
        args.live_readonly and status["authenticated"] is not True
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
