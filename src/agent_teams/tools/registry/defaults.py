# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.task_tools import TOOLS as TASK_TOOLS
from agent_teams.tools.workspace_tools import TOOLS as WORKSPACE_TOOLS


def build_default_registry() -> ToolRegistry:
    tools = {
        **TASK_TOOLS,
        **WORKSPACE_TOOLS,
    }
    return ToolRegistry(tools)
