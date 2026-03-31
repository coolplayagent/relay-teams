from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent

if TYPE_CHECKING:
    from agent_teams.tools.runtime import ToolDeps


_EXEC_SESSION_REGISTERED_ATTR = "_agent_teams_exec_session_registered"


def register_edit(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.edit import register as register_impl

    register_impl(agent)


def register_glob(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.glob import register as register_impl

    register_impl(agent)


def register_grep(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.grep import register as register_impl

    register_impl(agent)


def register_read(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.read import register as register_impl

    register_impl(agent)


def register_exec_session(agent: Agent[ToolDeps, str]) -> None:
    if bool(getattr(agent, _EXEC_SESSION_REGISTERED_ATTR, False)):
        return
    from agent_teams.tools.workspace_tools.exec_session import register as register_impl

    register_impl(agent)
    setattr(agent, _EXEC_SESSION_REGISTERED_ATTR, True)


def register_write(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.write import register as register_impl

    register_impl(agent)


TOOLS = {
    "edit": register_edit,
    "glob": register_glob,
    "grep": register_grep,
    "read": register_read,
    "write": register_write,
    "exec_command": register_exec_session,
    "list_exec_sessions": register_exec_session,
    "write_stdin": register_exec_session,
    "resize_exec_session": register_exec_session,
    "terminate_exec_session": register_exec_session,
}

__all__ = [
    "TOOLS",
    "register_edit",
    "register_glob",
    "register_grep",
    "register_read",
    "register_exec_session",
    "register_write",
]
