# -*- coding: utf-8 -*-
from __future__ import annotations

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
from agent_teams.computer.linux_runtime import LinuxDesktopRuntime
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
