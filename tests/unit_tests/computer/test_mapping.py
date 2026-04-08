# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.computer import (
    BUILTIN_COMPUTER_TOOL_NAMES,
    ComputerActionRisk,
    ComputerPermissionScope,
    ComputerRuntimeKind,
    ComputerWindow,
    build_computer_tool_payload,
    describe_builtin_tool,
    describe_external_acp_tool,
    describe_mcp_tool,
)
from relay_teams.mcp.mcp_models import McpConfigScope


def test_describe_builtin_tool_returns_expected_descriptor() -> None:
    descriptor = describe_builtin_tool("launch_app")

    assert descriptor is not None
    assert descriptor.runtime_kind == ComputerRuntimeKind.BUILTIN_TOOL
    assert descriptor.permission_scope == ComputerPermissionScope.APP_LAUNCH
    assert descriptor.risk_level == ComputerActionRisk.DESTRUCTIVE
    assert descriptor.source == "tool"
    assert "launch_app" in BUILTIN_COMPUTER_TOOL_NAMES


def test_describe_mcp_tool_distinguishes_app_and_session_scopes() -> None:
    app_descriptor = describe_mcp_tool(
        effective_tool_name="desktop_click",
        server_name="desktop",
        source_scope=McpConfigScope.APP,
    )
    session_descriptor = describe_mcp_tool(
        effective_tool_name="desktop_click",
        server_name="desktop",
        source_scope=McpConfigScope.SESSION,
    )

    assert app_descriptor is not None
    assert session_descriptor is not None
    assert app_descriptor.runtime_kind == ComputerRuntimeKind.APP_MCP
    assert session_descriptor.runtime_kind == ComputerRuntimeKind.SESSION_MCP_ACP
    assert app_descriptor.server_name == "desktop"
    assert session_descriptor.server_name == "desktop"


def test_describe_external_acp_tool_recognizes_aliases() -> None:
    descriptor = describe_external_acp_tool("press_key")

    assert descriptor is not None
    assert descriptor.runtime_kind == ComputerRuntimeKind.EXTERNAL_ACP
    assert descriptor.permission_scope == ComputerPermissionScope.KEYBOARD_SHORTCUT
    assert descriptor.source == "acp"


def test_build_computer_tool_payload_preserves_content_and_observation() -> None:
    descriptor = describe_builtin_tool("list_windows")
    assert descriptor is not None

    payload = build_computer_tool_payload(
        descriptor=descriptor,
        text="Listed windows.",
        content=(
            {
                "kind": "media_ref",
                "asset_id": "asset-1",
                "session_id": "session-1",
                "modality": "image",
                "mime_type": "image/png",
                "url": "/api/sessions/session-1/media/asset-1/file",
            },
        ),
        observation={
            "text": "snapshot",
            "focused_window": "Chrome DevTools",
            "windows": [
                ComputerWindow(
                    window_id="window-1",
                    app_name="Browser",
                    title="Chrome DevTools",
                    focused=True,
                ).model_dump(mode="json")
            ],
        },
        data={"window_count": 1},
    )

    assert payload["text"] == "Listed windows."
    computer = payload["computer"]
    assert isinstance(computer, dict)
    assert computer["source"] == "tool"
    assert computer["target_summary"] == ""
    assert payload["data"] == {"window_count": 1}
    assert payload["observation"] == {
        "text": "snapshot",
        "focused_window": "Chrome DevTools",
        "windows": [
            {
                "window_id": "window-1",
                "app_name": "Browser",
                "title": "Chrome DevTools",
                "focused": True,
            }
        ],
    }
