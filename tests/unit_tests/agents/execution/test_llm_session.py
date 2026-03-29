# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.agents.execution.llm_session import AgentLlmSession
from agent_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from agent_teams.mcp.mcp_registry import McpRegistry


def test_maybe_enrich_tool_result_payload_wraps_builtin_computer_results() -> None:
    session = object.__new__(AgentLlmSession)
    session._mcp_registry = McpRegistry()

    payload = AgentLlmSession._maybe_enrich_tool_result_payload(
        session,
        tool_name="capture_screen",
        result_payload={"ok": True, "data": {"text": "Captured."}},
    )

    assert isinstance(payload, dict)
    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    computer = data["computer"]
    assert isinstance(computer, dict)
    assert computer["source"] == "tool"
    assert computer["runtime_kind"] == "builtin_tool"


def test_maybe_enrich_tool_result_payload_wraps_session_mcp_results() -> None:
    session = object.__new__(AgentLlmSession)
    session._mcp_registry = McpRegistry(
        (
            McpServerSpec(
                name="desktop",
                config={},
                server_config={"transport": "stdio", "command": "desktop-mcp"},
                source=McpConfigScope.SESSION,
            ),
        )
    )

    payload = AgentLlmSession._maybe_enrich_tool_result_payload(
        session,
        tool_name="desktop_click",
        result_payload={"text": "Clicked."},
    )

    assert isinstance(payload, dict)
    computer = payload["computer"]
    assert isinstance(computer, dict)
    assert computer["source"] == "mcp"
    assert computer["runtime_kind"] == "session_mcp_acp"
