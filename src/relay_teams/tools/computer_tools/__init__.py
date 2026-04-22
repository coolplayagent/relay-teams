# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent

if TYPE_CHECKING:
    from relay_teams.tools.runtime.context import ToolDeps

_REGISTERED_AGENT_IDS: set[int] = set()


def register_computer_tools(agent: Agent[ToolDeps, str]) -> None:
    agent_id = id(agent)
    if agent_id in _REGISTERED_AGENT_IDS:
        return
    _REGISTERED_AGENT_IDS.add(agent_id)
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
