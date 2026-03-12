# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_teams.interfaces.server.deps import get_mcp_service
from agent_teams.interfaces.server.routers import mcp
from agent_teams.mcp.models import (
    McpConfigScope,
    McpServerSummary,
    McpServerToolsSummary,
    McpToolInfo,
)


class _FakeMcpService:
    def list_servers(self) -> tuple[McpServerSummary, ...]:
        return (
            McpServerSummary(
                name="filesystem",
                source=McpConfigScope.PROJECT,
                transport="stdio",
            ),
        )

    async def list_server_tools(self, name: str) -> McpServerToolsSummary:
        if name != "filesystem":
            raise ValueError(f"Unknown MCP server: {name}")
        return McpServerToolsSummary(
            server="filesystem",
            source=McpConfigScope.PROJECT,
            transport="stdio",
            tools=(McpToolInfo(name="read_file", description="Read a file"),),
        )


def _create_test_client(fake_service: object) -> TestClient:
    app = FastAPI()
    app.include_router(mcp.router, prefix="/api")
    app.dependency_overrides[get_mcp_service] = lambda: fake_service
    return TestClient(app)


def test_list_mcp_servers() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.get("/api/mcp/servers")

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "filesystem",
            "source": "project",
            "transport": "stdio",
        }
    ]


def test_list_mcp_server_tools() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.get("/api/mcp/servers/filesystem/tools")

    assert response.status_code == 200
    assert response.json() == {
        "server": "filesystem",
        "source": "project",
        "transport": "stdio",
        "tools": [{"name": "read_file", "description": "Read a file"}],
    }


def test_list_mcp_server_tools_surfaces_connection_failures() -> None:
    class _BrokenMcpService(_FakeMcpService):
        async def list_server_tools(self, name: str) -> McpServerToolsSummary:
            raise RuntimeError("Connection closed")

    client = _create_test_client(_BrokenMcpService())

    response = client.get("/api/mcp/servers/filesystem/tools")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Failed to load MCP tools for 'filesystem': Connection closed"
    }
