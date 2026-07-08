"""Gate Core orchestration."""

from .models import GateEvent, ReplyMode, SuggestedReply
from .router import GateRouter


class GateCore:
    def __init__(self, router: GateRouter, default_mode: ReplyMode = ReplyMode.MANUAL) -> None:
        self.router = router
        self.default_mode = default_mode

    def route_event(self, event: GateEvent) -> SuggestedReply:
        route = self.router.resolve(event)
        if route is None:
            return SuggestedReply(
                text="No route resolved. Manual handling required.",
                project_id="unresolved",
                mode=ReplyMode.MANUAL,
                metadata={"reason": "no_route"},
            )

        return SuggestedReply(
            text="Route resolved. Project adapter integration is pending.",
            project_id=route.project_id,
            mode=route.reply_mode_override or self.default_mode,
            metadata={"route_id": route.id},
        )
