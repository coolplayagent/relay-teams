# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.stage_tools import TOOLS as STAGE_TOOLS
from agent_teams.tools.workflow_tools import TOOLS as WORKFLOW_TOOLS
from agent_teams.tools.workspace_tools import TOOLS as WORKSPACE_TOOLS


def build_default_registry() -> ToolRegistry:
    tools = {
        **WORKFLOW_TOOLS,
        **WORKSPACE_TOOLS,
        **STAGE_TOOLS,
    }
    return ToolRegistry(tools)
