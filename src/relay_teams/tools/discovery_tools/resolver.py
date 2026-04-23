from __future__ import annotations

from relay_teams.tools.registry.registry import (
    ToolImplicitResolver,
    ToolResolutionContext,
)

ALWAYS_AVAILABLE_DISCOVERY_TOOLS: tuple[str, ...] = (
    "tool_search",
    "activate_tools",
)


class DiscoveryToolResolver(ToolImplicitResolver):
    def resolve_implicit_tools(
        self,
        context: ToolResolutionContext,
    ) -> tuple[str, ...]:
        del context
        return ALWAYS_AVAILABLE_DISCOVERY_TOOLS


__all__ = [
    "ALWAYS_AVAILABLE_DISCOVERY_TOOLS",
    "DiscoveryToolResolver",
]
