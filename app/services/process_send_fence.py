"""Process-wide baseline send fence, shared by every TelegramClient instance."""

from collections.abc import Iterator
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

    def require_admission(self) -> None:
        if not self.settings.gate_send_enabled:
            raise BaselineSendClosed("baseline_send_disabled")
        if self.observations_required and (
            self.last_observed is None or not 0 <= monotonic() - self.last_observed <= 15
        ):
            raise BaselineSendClosed("baseline_process_observation_unavailable")

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


def install_process_send_fence(fence: ProcessSendFence | None) -> ProcessSendFence | None:
    """Install before constructing workers; restore only after their shutdown."""
    global _process_fence
    previous, _process_fence = _process_fence, fence
    return previous
