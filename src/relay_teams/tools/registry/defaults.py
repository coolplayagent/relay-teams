# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.tools.discovery_tools import (
    DiscoveryToolResolver,
    TOOLS as DISCOVERY_TOOLS,
)
from relay_teams.tools.computer_tools import TOOLS as COMPUTER_TOOLS
from relay_teams.tools.im_tools.im_send import register as register_im_send
from relay_teams.tools.orchestration_tools import TOOLS as ORCHESTRATION_TOOLS
from relay_teams.tools.registry.registry import ToolRegistry
from relay_teams.tools.task_tools import TOOLS as TASK_TOOLS
from relay_teams.tools.todo_tools import TOOLS as TODO_TOOLS
from relay_teams.tools.web_tools import TOOLS as WEB_TOOLS
from relay_teams.tools.workspace_tools import TOOLS as WORKSPACE_TOOLS

IM_TOOLS = {
    "im_send": register_im_send,
}
HIDDEN_FROM_ROLE_CONFIG: tuple[str, ...] = (
    "im_send",
    "tool_search",
    "activate_tools",
)


def build_default_registry() -> ToolRegistry:
    tools = {
        **ORCHESTRATION_TOOLS,
        **TASK_TOOLS,
        **TODO_TOOLS,
        **WEB_TOOLS,
        **WORKSPACE_TOOLS,
        **COMPUTER_TOOLS,
        **DISCOVERY_TOOLS,
        **IM_TOOLS,
    }
    registry = ToolRegistry(
        tools,
        hidden_from_config=HIDDEN_FROM_ROLE_CONFIG,
    )
    registry.register_implicit_resolver(DiscoveryToolResolver())
    return registry
