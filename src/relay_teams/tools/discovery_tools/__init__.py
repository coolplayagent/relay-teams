from __future__ import annotations

from relay_teams.tools.discovery_tools.tool_search import (
    register as register_tool_search,
)
from relay_teams.tools.registry import ToolImplicitResolver, ToolResolutionContext

ALWAYS_AVAILABLE_DISCOVERY_TOOLS: tuple[str, ...] = ("tool_search",)


class DiscoveryToolResolver(ToolImplicitResolver):
    def resolve_implicit_tools(
        self,
        context: ToolResolutionContext,
    ) -> tuple[str, ...]:
        del context
        return ALWAYS_AVAILABLE_DISCOVERY_TOOLS


TOOLS = {
    "tool_search": register_tool_search,
}

__all__ = [
    "ALWAYS_AVAILABLE_DISCOVERY_TOOLS",
    "DiscoveryToolResolver",
    "TOOLS",
    "register_tool_search",
]
