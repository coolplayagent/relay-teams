# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.computer.models import (
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

from relay_teams.computer.mapping import (
    BUILTIN_COMPUTER_TOOL_NAMES,
    build_computer_tool_payload,
    describe_builtin_tool,
    describe_external_acp_tool,
    describe_mcp_tool,
)

from relay_teams.computer.runtime import (
    ComputerRuntime,
    DisabledComputerRuntime,
    ScriptedComputerRuntime,
    build_default_computer_runtime,
)

from relay_teams.computer.linux_runtime import LinuxDesktopRuntime

from relay_teams.computer.windows_runtime import WindowsDesktopRuntime

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
    "WindowsDesktopRuntime",
    "build_computer_tool_payload",
    "build_default_computer_runtime",
    "describe_builtin_tool",
    "describe_external_acp_tool",
    "describe_mcp_tool",
]
