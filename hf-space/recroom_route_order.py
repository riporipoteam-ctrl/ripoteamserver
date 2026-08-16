from __future__ import annotations

from typing import Any


def stabilize_recroom_route_order(app: Any) -> None:
    """Move dynamic room-id routes behind their static siblings.

    Starlette/FastAPI matches routes in declaration order. Both the unified
    `/Room_server/rooms/{room_id}` family and the old rooms.rec.net alias
    `/rooms/{room_id}` can otherwise consume words such as `search`, `hot`,
    `bulk`, `ownedby`, and return a 422 integer-validation error before the
    intended static route gets a chance to match.
    """
    routes = getattr(getattr(app, "router", None), "routes", None)
    if not isinstance(routes, list):
        return

    dynamic_prefixes = (
        "/Room_server/rooms/{room_id}",
        "/rooms/{room_id}",
    )
    dynamic = [
        route
        for route in routes
        if any(str(getattr(route, "path", "")).startswith(prefix) for prefix in dynamic_prefixes)
    ]
    if not dynamic:
        return
    routes[:] = [route for route in routes if route not in dynamic] + dynamic
