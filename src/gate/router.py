"""Route resolution for Gate events."""

from .models import GateEvent, Route


class GateRouter:
    def __init__(self, routes: list[Route] | None = None) -> None:
        self._routes = sorted(routes or [], key=lambda route: route.priority)

    def resolve(self, event: GateEvent) -> Route | None:
        for route in self._routes:
            if not route.is_active:
                continue
            if route.bot_id is not None and route.bot_id != event.bot_id:
                continue
            if route.account_id is not None and route.account_id != event.account_id:
                continue
            if route.chat_id is not None and route.chat_id != event.chat_id:
                continue
            if route.event_type is not None and route.event_type != event.event_type:
                continue
            return route
        return None
