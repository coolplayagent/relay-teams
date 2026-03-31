from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent

if TYPE_CHECKING:
    from agent_teams.tools.runtime import ToolDeps


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


def register_shell(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.shell import register as register_impl

    register_impl(agent)


def register_shell_background(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.shell_background import (
        register as register_impl,
    )

    register_impl(agent)


def register_write(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.write import register as register_impl

    register_impl(agent)


def register_write_tmp(agent: Agent[ToolDeps, str]) -> None:
    from agent_teams.tools.workspace_tools.write_tmp import register as register_impl

    register_impl(agent)


TOOLS = {
    "edit": register_edit,
    "glob": register_glob,
    "grep": register_grep,
    "read": register_read,
    "write": register_write,
    "write_tmp": register_write_tmp,
    "shell": register_shell,
    "shell_background_start": register_shell_background,
    "shell_background_list": register_shell_background,
    "shell_background_read": register_shell_background,
    "shell_background_wait": register_shell_background,
    "shell_background_write": register_shell_background,
    "shell_background_resize": register_shell_background,
    "shell_background_stop": register_shell_background,
}

__all__ = [
    "TOOLS",
    "register_edit",
    "register_glob",
    "register_grep",
    "register_read",
    "register_shell",
    "register_shell_background",
    "register_write",
    "register_write_tmp",
]
