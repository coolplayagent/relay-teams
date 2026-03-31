# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.computer.linux_runtime import LinuxDesktopRuntime
    from agent_teams.computer.mapping import (
        BUILTIN_COMPUTER_TOOL_NAMES,
        build_computer_tool_payload,
        describe_builtin_tool,
        describe_external_acp_tool,
        describe_mcp_tool,
    )
    from agent_teams.computer.models import (
        ComputerActionDescriptor,
        ComputerActionResult,
        ComputerActionRisk,
        ComputerActionTarget,
        ComputerActionType,
        ComputerObservation,
        ComputerPermissionScope,
        ComputerRuntimeKind,
        ComputerWindow,
        ExecutionSurface,
    )
    from agent_teams.computer.runtime import (
        ComputerRuntime,
        DisabledComputerRuntime,
        ScriptedComputerRuntime,
        build_default_computer_runtime,
    )

__all__ = [
    "BUILTIN_COMPUTER_TOOL_NAMES",
    "ComputerActionDescriptor",
    "ComputerActionResult",
    "ComputerActionRisk",
    "ComputerActionTarget",
    "ComputerActionType",
    "ComputerObservation",
    "ComputerPermissionScope",
    "ComputerRuntime",
    "ComputerRuntimeKind",
    "ComputerWindow",
    "DisabledComputerRuntime",
    "ExecutionSurface",
    "LinuxDesktopRuntime",
    "ScriptedComputerRuntime",
    "build_computer_tool_payload",
    "build_default_computer_runtime",
    "describe_builtin_tool",
    "describe_external_acp_tool",
    "describe_mcp_tool",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "BUILTIN_COMPUTER_TOOL_NAMES": (
        "agent_teams.computer.mapping",
        "BUILTIN_COMPUTER_TOOL_NAMES",
    ),
    "ComputerActionDescriptor": (
        "agent_teams.computer.models",
        "ComputerActionDescriptor",
    ),
    "ComputerActionResult": ("agent_teams.computer.models", "ComputerActionResult"),
    "ComputerActionRisk": ("agent_teams.computer.models", "ComputerActionRisk"),
    "ComputerActionTarget": ("agent_teams.computer.models", "ComputerActionTarget"),
    "ComputerActionType": ("agent_teams.computer.models", "ComputerActionType"),
    "ComputerObservation": ("agent_teams.computer.models", "ComputerObservation"),
    "ComputerPermissionScope": (
        "agent_teams.computer.models",
        "ComputerPermissionScope",
    ),
    "ComputerRuntime": ("agent_teams.computer.runtime", "ComputerRuntime"),
    "ComputerRuntimeKind": ("agent_teams.computer.models", "ComputerRuntimeKind"),
    "ComputerWindow": ("agent_teams.computer.models", "ComputerWindow"),
    "DisabledComputerRuntime": (
        "agent_teams.computer.runtime",
        "DisabledComputerRuntime",
    ),
    "ExecutionSurface": ("agent_teams.computer.models", "ExecutionSurface"),
    "LinuxDesktopRuntime": (
        "agent_teams.computer.linux_runtime",
        "LinuxDesktopRuntime",
    ),
    "ScriptedComputerRuntime": (
        "agent_teams.computer.runtime",
        "ScriptedComputerRuntime",
    ),
    "build_computer_tool_payload": (
        "agent_teams.computer.mapping",
        "build_computer_tool_payload",
    ),
    "build_default_computer_runtime": (
        "agent_teams.computer.runtime",
        "build_default_computer_runtime",
    ),
    "describe_builtin_tool": (
        "agent_teams.computer.mapping",
        "describe_builtin_tool",
    ),
    "describe_external_acp_tool": (
        "agent_teams.computer.mapping",
        "describe_external_acp_tool",
    ),
    "describe_mcp_tool": ("agent_teams.computer.mapping", "describe_mcp_tool"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
