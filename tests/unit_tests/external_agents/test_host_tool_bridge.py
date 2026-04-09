# -*- coding: utf-8 -*-
from __future__ import annotations

from types import ModuleType

import relay_teams.external_agents.host_tool_bridge as host_tool_bridge_module


class _PublicTool:
    pass


class _PublicToolResult:
    pass


class _LegacyTool:
    pass


class _LegacyToolResult:
    pass


def test_load_fastmcp_tool_types_prefers_public_exports(monkeypatch) -> None:
    public_module = ModuleType("fastmcp.tools")
    setattr(public_module, "Tool", _PublicTool)
    setattr(public_module, "ToolResult", _PublicToolResult)

    def fake_import_module(name: str) -> ModuleType:
        assert name == "fastmcp.tools"
        return public_module

    monkeypatch.setattr(
        host_tool_bridge_module.importlib, "import_module", fake_import_module
    )

    tool_cls, tool_result_cls = host_tool_bridge_module._load_fastmcp_tool_types()

    assert tool_cls is _PublicTool
    assert tool_result_cls is _PublicToolResult


def test_load_fastmcp_tool_types_falls_back_to_legacy_module(monkeypatch) -> None:
    public_module = ModuleType("fastmcp.tools")
    legacy_module = ModuleType("fastmcp.tools.tool")
    setattr(legacy_module, "Tool", _LegacyTool)
    setattr(legacy_module, "ToolResult", _LegacyToolResult)
    requested_modules: list[str] = []

    def fake_import_module(name: str) -> ModuleType:
        requested_modules.append(name)
        if name == "fastmcp.tools":
            return public_module
        if name == "fastmcp.tools.tool":
            return legacy_module
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr(
        host_tool_bridge_module.importlib, "import_module", fake_import_module
    )

    tool_cls, tool_result_cls = host_tool_bridge_module._load_fastmcp_tool_types()

    assert requested_modules == ["fastmcp.tools", "fastmcp.tools.tool"]
    assert tool_cls is _LegacyTool
    assert tool_result_cls is _LegacyToolResult
