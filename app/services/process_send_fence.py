"""Process-wide baseline send fence, shared by every TelegramClient instance."""

import hashlib
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic

from app.core.config import Settings, get_settings


class BaselineSendClosed(RuntimeError):
    """No HTTP attempt was started because baseline admission is closed."""


@dataclass
class ProcessSendFence:
    settings: Settings
    observations_required: bool = False
    observer_owned: bool = False
    last_observed: float | None = None
    inflight: int = 0
    closed: bool = False
    candidate_mode: bool = False
    runtime_owner: object | None = None
    candidate_health: Callable[[str], Awaitable[None]] | None = None

    def require_admission(self) -> None:
        if self.closed:
            raise BaselineSendClosed("process_admission_closed")
        if not self.settings.gate_send_enabled:
            raise BaselineSendClosed("baseline_send_disabled")
        if self.observations_required and (
            self.last_observed is None or not 0 <= monotonic() - self.last_observed <= 15
        ):
            raise BaselineSendClosed("baseline_process_observation_unavailable")

    async def require_write_admission(self, bot_prefix: str) -> None:
        """The actual client identity chooses scope; callers cannot assert health."""
        self.require_admission()
        if not self.candidate_mode:
            return
        if not bot_prefix.isdecimal() or int(bot_prefix) <= 0:
            raise BaselineSendClosed("process_bot_identity_unknown")
        primary = self.settings.telegram_bot_token.strip().split(":", 1)[0]
        secondary = {
            getattr(self.settings, f"telegram_{name}_bot_token")
            .get_secret_value()
            .strip()
            .split(":", 1)[0]
            for name in ("alpha", "beta", "become", "aqub")
        } - {""}
        if bot_prefix == primary:
            if bot_prefix in secondary or self.candidate_health is None:
                raise BaselineSendClosed("process_primary_admission_unavailable")
            resource = hashlib.sha256(("telegram-bot:" + bot_prefix).encode()).hexdigest()
            try:
                await self.candidate_health(resource)
            except Exception:
                raise BaselineSendClosed("process_verified_health_unavailable") from None
        elif bot_prefix not in secondary:
            raise BaselineSendClosed("process_bot_identity_unknown")
        # Distinct configured product bots retain their own admission contracts.

    @contextmanager
    def admitted(self) -> Iterator[None]:
        # No await between admission and accounting. All runtime callers share
        # one asyncio event loop; the counter covers the complete HTTP await.
        self.require_admission()
        self.inflight += 1
        try:
            yield
        finally:
            self.inflight -= 1


_process_fence: ProcessSendFence | None = None


def process_send_fence() -> ProcessSendFence:
    global _process_fence
    if _process_fence is None:
        settings = get_settings()
        _process_fence = ProcessSendFence(
            settings, observations_required=settings.gate_env == "production"
        )
    return _process_fence


def installed_process_send_fence() -> ProcessSendFence | None:
    """Return only an installed owner; do not manufacture observed health."""
    return _process_fence


def install_process_send_fence(fence: ProcessSendFence | None) -> ProcessSendFence | None:
    """Install before constructing workers; restore only after their shutdown."""
    global _process_fence
    previous, _process_fence = _process_fence, fence
    return previous
