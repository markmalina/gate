"""Truthful default-off server view of the owner-only MTProto boundary."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from app.personal_telegram.cloud_session import cloud_binding_enabled, inspect_cloud_session


def personal_telegram_server_status(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The local DPAPI enrollment is not mounted or observed by Gate runtime."""
    environment = os.environ if environment is None else environment
    if cloud_binding_enabled(environment):
        return inspect_cloud_session(environment)
    return {
        "configured": None,
        "authenticated": None,
        "runtime_connected": False,
        "send_enabled": False,
        "create_group_enabled": False,
        "last_successful_check": None,
        "error_code": "local_session_not_bound",
    }

