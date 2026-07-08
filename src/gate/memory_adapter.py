"""Memory adapter boundary for recording communication facts."""

from typing import Protocol

from .models import GateEvent, SuggestedReply


class MemoryAdapter(Protocol):
    def record_communication_fact(self, event: GateEvent, reply: SuggestedReply, status: str) -> None:
        """Record who was contacted, what was sent, when and what the next step is."""
        ...
