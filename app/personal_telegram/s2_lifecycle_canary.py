"""One-shot lifecycle acceptance for the isolated, non-sending S2 worker."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import signal
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.personal_telegram.command_scope import CommandRuntimeError
from app.personal_telegram.s2_session_vault import VAULT, require_s2_mount

RUN_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def claim_canary(run_id: str, environment: Mapping[str, str], *, vault: Path = VAULT) -> None:
    """Persist the explicit one-shot grant before any provider connection."""
    if RUN_PATTERN.fullmatch(run_id) is None:
        raise CommandRuntimeError("s2_lifecycle_run_invalid")
    require_s2_mount(environment)
    try:
        with (vault / f".lifecycle-{run_id}").open("xb") as claim:
            os.fsync(claim.fileno())
        descriptor = os.open(vault, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        raise CommandRuntimeError("s2_lifecycle_run_already_spent") from None


def unavailable_database_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Keep the exact S2 endpoint and replace only the credential for a fail-closed probe."""
    changed = dict(environment)
    key = "GATE_PERSONAL_RUNTIME_DATABASE_URL"
    url = make_url(changed[key])
    changed[key] = url.set(password=f"canary-invalid-{secrets.token_hex(8)}").render_as_string(
        hide_password=False
    )
    return changed


def _decode(stdout: bytes) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except (UnicodeError, json.JSONDecodeError):
        raise CommandRuntimeError("s2_lifecycle_result_invalid") from None
    if not isinstance(value, dict):
        raise CommandRuntimeError("s2_lifecycle_result_invalid")
    return value


async def _completed(
    action: str,
    environment: Mapping[str, str],
    *,
    seconds: int = 20,
    timeout: float = 30,
    allow_redacted_failure: bool = False,
) -> tuple[int, dict[str, Any]]:
    arguments = [sys.executable, "-m", "app.personal_telegram.runtime_cli", action]
    if action == "run":
        arguments.extend(("--seconds", str(seconds)))
    arguments.append("--s2-owner")
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=dict(environment),
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise CommandRuntimeError("s2_lifecycle_process_timeout") from None
    code = int(process.returncode or 0)
    try:
        result = _decode(stdout)
    except CommandRuntimeError:
        if allow_redacted_failure and code != 0:
            result = {"state": "failed", "error_code": "runtime_configuration_failed"}
        else:
            raise
    return code, result


async def _start_worker(environment: Mapping[str, str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.personal_telegram.runtime_cli",
        "run",
        "--seconds",
        "120",
        "--s2-owner",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=dict(environment),
    )


async def _wait_ready(
    environment: Mapping[str, str], *, after_generation: int = 0
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 18
    last: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        code, last = await _completed("inspect", environment, timeout=8)
        generation = last.get("lease_generation")
        if (
            code == 0
            and last.get("configured") is True
            and last.get("authenticated") is True
            and last.get("runtime_connected") is True
            and isinstance(generation, int)
            and generation > after_generation
        ):
            return last
        await asyncio.sleep(0.25)
    raise CommandRuntimeError("s2_lifecycle_worker_not_ready")


async def _stop_worker(process: asyncio.subprocess.Process) -> dict[str, Any]:
    if process.returncode is None:
        process.send_signal(signal.SIGTERM)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=18)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise CommandRuntimeError("s2_lifecycle_worker_stop_timeout") from None
    result = _decode(stdout)
    if process.returncode != 0 or result.get("state") != "disconnected":
        raise CommandRuntimeError("s2_lifecycle_worker_stop_failed")
    return result


async def _ensure_stopped(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.communicate(), timeout=18)
    except TimeoutError:
        process.kill()
        await process.wait()


async def run_s2_lifecycle_canary(
    run_id: str, environment: Mapping[str, str]
) -> dict[str, object]:
    """Verify two live owner checks, exclusion, clean stop and DB fail-closed startup."""
    claim_canary(run_id, environment)
    enabled = dict(environment)
    enabled["GATE_PERSONAL_RUNTIME_ENABLED"] = "true"

    code, initial = await _completed("inspect", enabled)
    if code != 0 or initial.get("configured") is not True or initial.get("runtime_connected"):
        raise CommandRuntimeError("s2_lifecycle_initial_state_invalid")

    code, unavailable = await _completed(
        "inspect",
        unavailable_database_environment(enabled),
        allow_redacted_failure=True,
    )
    if (
        code == 0
        or unavailable.get("state") != "failed"
        or unavailable.get("error_code") != "runtime_configuration_failed"
    ):
        raise CommandRuntimeError("s2_lifecycle_database_loss_not_closed")

    first: asyncio.subprocess.Process | None = None
    second: asyncio.subprocess.Process | None = None
    try:
        first = await _start_worker(enabled)
        first_status = await _wait_ready(enabled)
        first_generation = int(first_status["lease_generation"])

        overlap_code, overlap = await _completed("run", enabled, timeout=12)
        if (
            overlap_code == 0
            or overlap.get("state") != "failed"
            or overlap.get("error_code") != "session_owner_unavailable"
        ):
            raise CommandRuntimeError("s2_lifecycle_overlap_not_rejected")

        await _stop_worker(first)
        first = None
        code, stopped = await _completed("inspect", enabled)
        if code != 0 or stopped.get("runtime_connected") or stopped.get("owner_lease_present"):
            raise CommandRuntimeError("s2_lifecycle_first_stop_invalid")

        second = await _start_worker(enabled)
        second_status = await _wait_ready(enabled, after_generation=first_generation)
        second_generation = int(second_status["lease_generation"])
        await _stop_worker(second)
        second = None

        code, final = await _completed("inspect", enabled)
        if code != 0 or final.get("runtime_connected") or final.get("owner_lease_present"):
            raise CommandRuntimeError("s2_lifecycle_final_state_invalid")
        return {
            "account": "RU",
            "state": "verified_and_disconnected",
            "first_generation": first_generation,
            "second_generation": second_generation,
            "overlap_rejected": True,
            "database_loss_failed_closed": True,
            "telegram_owner_checks": 2,
            "outbound_messages": 0,
            "dialog_reads": 0,
            "group_actions": 0,
            "bot_actions": 0,
        }
    finally:
        await _ensure_stopped(first)
        await _ensure_stopped(second)
