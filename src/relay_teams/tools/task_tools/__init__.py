from __future__ import annotations

from relay_teams.tools.task_tools.ask_question import register as register_ask_question
from relay_teams.tools.task_tools.spawn_subagent import (
    register as register_spawn_subagent,
)

TOOLS = {
    "ask_question": register_ask_question,
    "spawn_subagent": register_spawn_subagent,
}

__all__ = [
    "TOOLS",
    "register_ask_question",
    "register_spawn_subagent",
]
