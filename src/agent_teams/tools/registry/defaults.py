# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.tools.feishu_tools.feishu_send import register as register_feishu_send
from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.task_tools import TOOLS as TASK_TOOLS
from agent_teams.tools.web_tools import TOOLS as WEB_TOOLS
from agent_teams.tools.workspace_tools import TOOLS as WORKSPACE_TOOLS

FEISHU_TOOLS = {"feishu_send": register_feishu_send}


def build_default_registry() -> ToolRegistry:
    tools = {
        **TASK_TOOLS,
        **WEB_TOOLS,
        **WORKSPACE_TOOLS,
        **FEISHU_TOOLS,
    }
    return ToolRegistry(tools)
