"""Truthful default-off server view of the owner-only MTProto boundary."""

from __future__ import annotations

from typing import Any


def personal_telegram_server_status() -> dict[str, Any]:
    """The local DPAPI enrollment is not mounted or observed by Gate runtime."""
    return {
        "configured": None,
        "authenticated": None,
        "runtime_connected": False,
        "send_enabled": False,
        "create_group_enabled": False,
        "last_successful_check": None,
        "error_code": "local_session_not_bound",
    }
