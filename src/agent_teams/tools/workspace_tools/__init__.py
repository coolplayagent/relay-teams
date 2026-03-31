from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent

if TYPE_CHECKING:
    from agent_teams.tools.runtime import ToolDeps


_EXEC_SESSION_REGISTERED_ATTR = "_agent_teams_exec_session_registered_names"


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
    _register_exec_session_tools(
        agent,
        (
            "exec_command",
            "list_exec_sessions",
            "write_stdin",
            "resize_exec_session",
            "terminate_exec_session",
        ),
    )


def register_exec_command(agent: Agent[ToolDeps, str]) -> None:
    _register_exec_session_tools(agent, ("exec_command",))


def register_list_exec_sessions(agent: Agent[ToolDeps, str]) -> None:
    _register_exec_session_tools(agent, ("list_exec_sessions",))


def register_write_stdin(agent: Agent[ToolDeps, str]) -> None:
    _register_exec_session_tools(agent, ("write_stdin",))


def register_resize_exec_session(agent: Agent[ToolDeps, str]) -> None:
    _register_exec_session_tools(agent, ("resize_exec_session",))


def register_terminate_exec_session(agent: Agent[ToolDeps, str]) -> None:
    _register_exec_session_tools(agent, ("terminate_exec_session",))


def register_write(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.write import register as register_impl

    register_impl(agent)


def _register_exec_session_tools(
    agent: Agent[ToolDeps, str],
    requested_tools: tuple[str, ...],
) -> None:
    registered = frozenset(getattr(agent, _EXEC_SESSION_REGISTERED_ATTR, ()))
    missing_tools = tuple(
        tool_name for tool_name in requested_tools if tool_name not in registered
    )
    if not missing_tools:
        return
    from agent_teams.tools.workspace_tools.exec_session import register as register_impl

    register_impl(agent, tool_names=missing_tools)
    setattr(
        agent,
        _EXEC_SESSION_REGISTERED_ATTR,
        tuple(sorted(set(registered) | set(missing_tools))),
    )


TOOLS = {
    "edit": register_edit,
    "glob": register_glob,
    "grep": register_grep,
    "read": register_read,
    "write": register_write,
    "exec_command": register_exec_command,
    "list_exec_sessions": register_list_exec_sessions,
    "write_stdin": register_write_stdin,
    "resize_exec_session": register_resize_exec_session,
    "terminate_exec_session": register_terminate_exec_session,
}

__all__ = [
    "TOOLS",
    "register_edit",
    "register_glob",
    "register_grep",
    "register_read",
    "register_exec_session",
    "register_exec_command",
    "register_list_exec_sessions",
    "register_write_stdin",
    "register_resize_exec_session",
    "register_terminate_exec_session",
    "register_write",
]
