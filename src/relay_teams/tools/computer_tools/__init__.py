# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent

if TYPE_CHECKING:
    from relay_teams.tools.runtime import ToolDeps

_REGISTERED_MARKER_ATTR = "_relay_teams_computer_tools_registered"


def register_computer_tools(agent: Agent[ToolDeps, str]) -> None:
    if bool(getattr(agent, _REGISTERED_MARKER_ATTR, False)):
        return
    setattr(agent, _REGISTERED_MARKER_ATTR, True)
    from relay_teams.tools.computer_tools.runtime import register as register_impl

    register_impl(agent)


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
