from __future__ import annotations

from relay_teams.tools.discovery_tools.activate_tools import (
    register as register_activate_tools,
)
from relay_teams.tools.discovery_tools.resolver import (
    ALWAYS_AVAILABLE_DISCOVERY_TOOLS,
    DiscoveryToolResolver,
)
from relay_teams.tools.discovery_tools.tool_search import (
    register as register_tool_search,
)


TOOLS = {
    "activate_tools": register_activate_tools,
    "tool_search": register_tool_search,
}

__all__ = [
    "ALWAYS_AVAILABLE_DISCOVERY_TOOLS",
    "DiscoveryToolResolver",
    "TOOLS",
    "register_activate_tools",
    "register_tool_search",
]
