"""Project adapter boundary.

Project adapters connect Gate to Alpha, Mayak, SysteM, M Stack and future agents.
"""

from typing import Protocol

from .models import GateEvent, SuggestedReply


class ProjectAdapter(Protocol):
    def suggest_reply(self, event: GateEvent) -> SuggestedReply:
        """Return suggested reply for a normalized Gate event."""
        ...
