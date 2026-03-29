# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from agent_teams.computer.models import (
    ComputerActionDescriptor,
    ComputerActionRisk,
    ComputerActionTarget,
    ComputerActionType,
    ComputerPermissionScope,
    ComputerRuntimeKind,
    ExecutionSurface,
)
from agent_teams.mcp.mcp_models import McpConfigScope

_BUILTIN_TOOL_MAP: dict[
    str,
    tuple[ComputerActionType, ComputerPermissionScope, ComputerActionRisk],
] = {
    "capture_screen": (
        ComputerActionType.CAPTURE_SCREEN,
        ComputerPermissionScope.OBSERVE,
        ComputerActionRisk.SAFE,
    ),
    "list_windows": (
        ComputerActionType.LIST_WINDOWS,
        ComputerPermissionScope.OBSERVE,
        ComputerActionRisk.SAFE,
    ),
    "focus_window": (
        ComputerActionType.FOCUS_WINDOW,
        ComputerPermissionScope.WINDOW_MANAGEMENT,
        ComputerActionRisk.GUARDED,
    ),
    "click_at": (
        ComputerActionType.CLICK,
        ComputerPermissionScope.POINTER,
        ComputerActionRisk.GUARDED,
    ),
    "double_click_at": (
        ComputerActionType.DOUBLE_CLICK,
        ComputerPermissionScope.POINTER,
        ComputerActionRisk.GUARDED,
    ),
    "drag_between": (
        ComputerActionType.DRAG,
        ComputerPermissionScope.DESTRUCTIVE,
        ComputerActionRisk.DESTRUCTIVE,
    ),
    "type_text": (
        ComputerActionType.TYPE_TEXT,
        ComputerPermissionScope.INPUT_TEXT,
        ComputerActionRisk.GUARDED,
    ),
    "scroll_view": (
        ComputerActionType.SCROLL,
        ComputerPermissionScope.POINTER,
        ComputerActionRisk.GUARDED,
    ),
    "hotkey": (
        ComputerActionType.HOTKEY,
        ComputerPermissionScope.KEYBOARD_SHORTCUT,
        ComputerActionRisk.GUARDED,
    ),
    "launch_app": (
        ComputerActionType.LAUNCH_APP,
        ComputerPermissionScope.APP_LAUNCH,
        ComputerActionRisk.DESTRUCTIVE,
    ),
    "wait_for_window": (
        ComputerActionType.WAIT_FOR_WINDOW,
        ComputerPermissionScope.OBSERVE,
        ComputerActionRisk.SAFE,
    ),
}

_MCP_SUFFIX_ALIASES: dict[
    str,
    tuple[ComputerActionType, ComputerPermissionScope, ComputerActionRisk],
] = {
    "capture_screen": _BUILTIN_TOOL_MAP["capture_screen"],
    "take_screenshot": _BUILTIN_TOOL_MAP["capture_screen"],
    "screenshot": _BUILTIN_TOOL_MAP["capture_screen"],
    "list_windows": _BUILTIN_TOOL_MAP["list_windows"],
    "focus_window": _BUILTIN_TOOL_MAP["focus_window"],
    "click": _BUILTIN_TOOL_MAP["click_at"],
    "click_at": _BUILTIN_TOOL_MAP["click_at"],
    "double_click": _BUILTIN_TOOL_MAP["double_click_at"],
    "double_click_at": _BUILTIN_TOOL_MAP["double_click_at"],
    "drag": _BUILTIN_TOOL_MAP["drag_between"],
    "drag_between": _BUILTIN_TOOL_MAP["drag_between"],
    "type": _BUILTIN_TOOL_MAP["type_text"],
    "type_text": _BUILTIN_TOOL_MAP["type_text"],
    "scroll": _BUILTIN_TOOL_MAP["scroll_view"],
    "scroll_view": _BUILTIN_TOOL_MAP["scroll_view"],
    "hotkey": _BUILTIN_TOOL_MAP["hotkey"],
    "press_key": _BUILTIN_TOOL_MAP["hotkey"],
    "launch_app": _BUILTIN_TOOL_MAP["launch_app"],
    "wait_for_window": _BUILTIN_TOOL_MAP["wait_for_window"],
}

BUILTIN_COMPUTER_TOOL_NAMES = frozenset(_BUILTIN_TOOL_MAP.keys())


def describe_builtin_tool(tool_name: str) -> ComputerActionDescriptor | None:
    mapping = _BUILTIN_TOOL_MAP.get(tool_name)
    if mapping is None:
        return None
    action, permission_scope, risk_level = mapping
    return ComputerActionDescriptor(
        action=action,
        runtime_kind=ComputerRuntimeKind.BUILTIN_TOOL,
        execution_surface=ExecutionSurface.DESKTOP,
        permission_scope=permission_scope,
        risk_level=risk_level,
        source="tool",
        target=ComputerActionTarget(),
    )


def describe_mcp_tool(
    *,
    effective_tool_name: str,
    server_name: str,
    source_scope: McpConfigScope,
) -> ComputerActionDescriptor | None:
    prefix = f"{server_name}_"
    suffix = (
        effective_tool_name[len(prefix) :]
        if effective_tool_name.startswith(prefix)
        else effective_tool_name
    )
    mapping = _MCP_SUFFIX_ALIASES.get(suffix)
    if mapping is None:
        return None
    action, permission_scope, risk_level = mapping
    return ComputerActionDescriptor(
        action=action,
        runtime_kind=(
            ComputerRuntimeKind.APP_MCP
            if source_scope == McpConfigScope.APP
            else ComputerRuntimeKind.SESSION_MCP_ACP
        ),
        execution_surface=ExecutionSurface.DESKTOP,
        permission_scope=permission_scope,
        risk_level=risk_level,
        source="mcp",
        server_name=server_name,
        target=ComputerActionTarget(),
    )


def describe_external_acp_tool(tool_name: str) -> ComputerActionDescriptor | None:
    normalized = tool_name.strip().casefold()
    mapping = _MCP_SUFFIX_ALIASES.get(normalized)
    if mapping is None:
        return None
    action, permission_scope, risk_level = mapping
    return ComputerActionDescriptor(
        action=action,
        runtime_kind=ComputerRuntimeKind.EXTERNAL_ACP,
        execution_surface=ExecutionSurface.DESKTOP,
        permission_scope=permission_scope,
        risk_level=risk_level,
        source="acp",
        target=ComputerActionTarget(),
    )


def build_computer_tool_payload(
    *,
    descriptor: ComputerActionDescriptor,
    text: str,
    content: tuple[dict[str, JsonValue], ...] = (),
    observation: dict[str, JsonValue] | None = None,
    data: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "text": text,
        "computer": descriptor.to_payload(),
    }
    if content:
        payload["content"] = list(content)
    if observation:
        payload["observation"] = dict(observation)
    if data:
        payload["data"] = dict(data)
    return payload
