# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai import Agent

from relay_teams.tools.runtime.context import ToolDeps

_COMPUTER_REGISTERED_ATTR = "_agent_teams_computer_tools_registered"


def register_computer_tools(agent: Agent[ToolDeps, str]) -> None:
    if bool(getattr(agent, _COMPUTER_REGISTERED_ATTR, False)):
        return
    from relay_teams.tools.computer_tools.runtime import register as register_impl

    register_impl(agent)
    setattr(agent, _COMPUTER_REGISTERED_ATTR, True)


TOOLS = {
    "capture_screen": register_computer_tools,
    "list_windows": register_computer_tools,
    "focus_window": register_computer_tools,
    "click_at": register_computer_tools,
    "double_click_at": register_computer_tools,
    "drag_between": register_computer_tools,
    "type_text": register_computer_tools,
    "scroll_view": register_computer_tools,
    "hotkey": register_computer_tools,
    "launch_app": register_computer_tools,
    "wait_for_window": register_computer_tools,
}

__all__ = [
    "TOOLS",
    "register_computer_tools",
]
