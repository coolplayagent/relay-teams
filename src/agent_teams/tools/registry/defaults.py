# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.tools.computer_tools import TOOLS as COMPUTER_TOOLS
from agent_teams.tools.im_tools.im_send import register as register_im_send
from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.task_tools import TOOLS as TASK_TOOLS
from agent_teams.tools.web_tools import TOOLS as WEB_TOOLS
from agent_teams.tools.workspace_tools import TOOLS as WORKSPACE_TOOLS

IM_TOOLS = {
    "im_send": register_im_send,
}
HIDDEN_FROM_ROLE_CONFIG: tuple[str, ...] = ("im_send",)
LEGACY_TOOL_ALIASES = {
    "shell": "exec_command",
}


def build_default_registry() -> ToolRegistry:
    tools = {
        **TASK_TOOLS,
        **WEB_TOOLS,
        **WORKSPACE_TOOLS,
        **COMPUTER_TOOLS,
        **IM_TOOLS,
    }
    return ToolRegistry(
        tools,
        hidden_from_config=HIDDEN_FROM_ROLE_CONFIG,
        legacy_aliases=LEGACY_TOOL_ALIASES,
    )
