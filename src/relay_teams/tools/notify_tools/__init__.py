from __future__ import annotations

from relay_teams.tools.notify_tools.notify import register as register_notify

TOOLS = {
    "notify": register_notify,
}

__all__ = [
    "TOOLS",
    "register_notify",
]
