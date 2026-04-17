from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent

if TYPE_CHECKING:
    from relay_teams.tools.runtime import ToolDeps


_WORKSPACE_REGISTERED_ATTR = "_agent_teams_workspace_registered_names"


def register_edit(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("edit",))


def register_glob(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("glob",))


def register_grep(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("grep",))


def register_read(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("read",))


def register_office_read_markdown(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("office_read_markdown",))


def register_notebook_edit(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("notebook_edit",))


def register_shell(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("shell",))


def register_list_background_tasks(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("list_background_tasks",))


def register_wait_background_task(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("wait_background_task",))


def register_stop_background_task(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("stop_background_task",))


def register_create_monitor(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("create_monitor",))


def register_list_monitors(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("list_monitors",))


def register_stop_monitor(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("stop_monitor",))


def register_spawn_subagent(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("spawn_subagent",))


def register_background_tasks(agent: Agent[ToolDeps, str]) -> None:
    register_shell(agent)
    register_spawn_subagent(agent)
    register_list_background_tasks(agent)
    register_wait_background_task(agent)
    register_stop_background_task(agent)


def register_monitors(agent: Agent[ToolDeps, str]) -> None:
    register_create_monitor(agent)
    register_list_monitors(agent)
    register_stop_monitor(agent)


def register_write(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("write",))


def register_write_tmp(agent: Agent[ToolDeps, str]) -> None:
    _register_workspace_tools(agent, ("write_tmp",))


def _register_workspace_tools(
    agent: Agent[ToolDeps, str],
    requested_tools: tuple[str, ...],
) -> None:
    registered = frozenset(getattr(agent, _WORKSPACE_REGISTERED_ATTR, ()))
    missing_tools = tuple(
        tool_name for tool_name in requested_tools if tool_name not in registered
    )
    if not missing_tools:
        return
    for tool_name in missing_tools:
        _register_single_tool(agent, tool_name)
    setattr(
        agent,
        _WORKSPACE_REGISTERED_ATTR,
        tuple(sorted(set(registered) | set(missing_tools))),
    )


def _register_single_tool(agent: Agent[ToolDeps, str], tool_name: str) -> None:
    if tool_name == "edit":
        from relay_teams.tools.workspace_tools.edit import register as register_impl
    elif tool_name == "glob":
        from relay_teams.tools.workspace_tools.glob import register as register_impl
    elif tool_name == "grep":
        from relay_teams.tools.workspace_tools.grep import register as register_impl
    elif tool_name == "read":
        from relay_teams.tools.workspace_tools.read import register as register_impl
    elif tool_name == "office_read_markdown":
        from relay_teams.tools.workspace_tools.office_read_markdown import (
            register as register_impl,
        )
    elif tool_name == "notebook_edit":
        from relay_teams.tools.workspace_tools.notebook_edit import (
            register as register_impl,
        )
    elif tool_name == "write":
        from relay_teams.tools.workspace_tools.write import register as register_impl
    elif tool_name == "write_tmp":
        from relay_teams.tools.workspace_tools.write_tmp import (
            register as register_impl,
        )
    elif tool_name == "shell":
        from relay_teams.tools.workspace_tools.shell import register as register_impl
    elif tool_name == "list_background_tasks":
        from relay_teams.tools.workspace_tools.list_background_tasks import (
            register as register_impl,
        )
    elif tool_name == "wait_background_task":
        from relay_teams.tools.workspace_tools.wait_background_task import (
            register as register_impl,
        )
    elif tool_name == "stop_background_task":
        from relay_teams.tools.workspace_tools.stop_background_task import (
            register as register_impl,
        )
    elif tool_name == "create_monitor":
        from relay_teams.tools.workspace_tools.create_monitor import (
            register as register_impl,
        )
    elif tool_name == "list_monitors":
        from relay_teams.tools.workspace_tools.list_monitors import (
            register as register_impl,
        )
    elif tool_name == "stop_monitor":
        from relay_teams.tools.workspace_tools.stop_monitor import (
            register as register_impl,
        )
    elif tool_name == "spawn_subagent":
        from relay_teams.tools.workspace_tools.spawn_subagent import (
            register as register_impl,
        )
    else:
        raise ValueError(f"Unknown workspace tool: {tool_name}")
    register_impl(agent)


TOOLS = {
    "edit": register_edit,
    "glob": register_glob,
    "grep": register_grep,
    "read": register_read,
    "office_read_markdown": register_office_read_markdown,
    "notebook_edit": register_notebook_edit,
    "write": register_write,
    "write_tmp": register_write_tmp,
    "shell": register_shell,
    "spawn_subagent": register_spawn_subagent,
    "list_background_tasks": register_list_background_tasks,
    "wait_background_task": register_wait_background_task,
    "stop_background_task": register_stop_background_task,
    "create_monitor": register_create_monitor,
    "list_monitors": register_list_monitors,
    "stop_monitor": register_stop_monitor,
}

__all__ = [
    "TOOLS",
    "register_background_tasks",
    "register_create_monitor",
    "register_edit",
    "register_glob",
    "register_grep",
    "register_list_background_tasks",
    "register_list_monitors",
    "register_monitors",
    "register_notebook_edit",
    "register_office_read_markdown",
    "register_read",
    "register_shell",
    "register_spawn_subagent",
    "register_stop_background_task",
    "register_stop_monitor",
    "register_wait_background_task",
    "register_write",
    "register_write_tmp",
]
