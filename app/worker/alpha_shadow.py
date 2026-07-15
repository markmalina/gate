"""Default-disabled composition boundary for one Alpha shadow trigger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.application.alpha_shadow_worker import (
    AlphaShadowDisposition,
    AlphaShadowWorkerError,
    AlphaShadowWorkerResult,
    ProcessAlphaShadowMessageCommand,
)
from app.core.config import Settings


class AlphaShadowServicePort(Protocol):
    async def execute(
        self, command: ProcessAlphaShadowMessageCommand
    ) -> AlphaShadowWorkerResult: ...


@dataclass(frozen=True, slots=True)
class AlphaShadowRuntimeResult:
    enabled: bool
    disposition: str
    code: str
    turn_key: str | None = None


async def run_alpha_shadow_trigger(
    *,
    settings: Settings,
    service: AlphaShadowServicePort,
    trigger_message_id: str,
    now: datetime | None = None,
) -> AlphaShadowRuntimeResult:
    """Fail closed before touching storage unless both release switches are on."""

    if (
        not settings.gate_alpha_connector_enabled
        or not settings.gate_alpha_shadow_worker_enabled
    ):
        return AlphaShadowRuntimeResult(
            enabled=False,
            disposition="disabled",
            code="alpha_shadow_disabled",
        )
    if not settings.telegram_alpha_bot_username.strip():
        return AlphaShadowRuntimeResult(
            enabled=True,
            disposition=AlphaShadowDisposition.BLOCKED.value,
            code="alpha_bot_username_missing",
        )
    try:
        result = await service.execute(
            ProcessAlphaShadowMessageCommand(
                workspace_key=settings.gate_workspace_id,
                trigger_message_id=trigger_message_id,
                bot_username=settings.telegram_alpha_bot_username,
                now=now or datetime.now(UTC),
            )
        )
    except AlphaShadowWorkerError as exc:
        return AlphaShadowRuntimeResult(
            enabled=True,
            disposition=AlphaShadowDisposition.BLOCKED.value,
            code=exc.code,
        )
    return AlphaShadowRuntimeResult(
        enabled=True,
        disposition=result.disposition.value,
        code=result.code,
        turn_key=result.turn_key,
    )
