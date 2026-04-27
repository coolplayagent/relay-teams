# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.deps import get_mcp_service
from relay_teams.interfaces.server.routers import mcp
from relay_teams.mcp.mcp_models import (
    McpConfigScope,
    McpServerAddResult,
    McpServerConfigResult,
    McpServerConnectionTestResult,
    McpServerEnabledUpdateRequest,
    McpServerSummary,
    McpServerToolsSummary,
    McpServerUpdateRequest,
    McpToolInfo,
)


class _FakeMcpService:
    def add_server(
        self,
        *,
        name: str,
        server_config: dict[str, object],
        overwrite: bool = False,
    ) -> McpServerAddResult:
        _ = overwrite
        return McpServerAddResult(
            server=McpServerSummary(
                name=name,
                source=McpConfigScope.APP,
                transport=str(server_config.get("transport", "stdio")),
            ),
            config_path="C:/Users/test/.relay-teams/mcp.json",
        )

    def get_server_config(self, name: str) -> McpServerConfigResult:
        if name != "filesystem":
            raise ValueError(f"Unknown MCP server: {name}")
        return McpServerConfigResult(
            server=McpServerSummary(
                name=name,
                source=McpConfigScope.APP,
                transport="stdio",
            ),
            config={
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            },
        )

    def update_server(
        self,
        name: str,
        request: McpServerUpdateRequest,
    ) -> McpServerConfigResult:
        if name != "filesystem":
            raise ValueError(f"Unknown MCP server: {name}")
        return McpServerConfigResult(
            server=McpServerSummary(
                name=name,
                source=McpConfigScope.APP,
                transport=str(request.config.get("transport", "stdio")),
            ),
            config=request.config,
        )

    def set_server_enabled(
        self,
        name: str,
        request: McpServerEnabledUpdateRequest,
    ) -> McpServerSummary:
        if name != "filesystem":
            raise ValueError(f"Unknown MCP server: {name}")
        return McpServerSummary(
            name=name,
            source=McpConfigScope.APP,
            transport="stdio",
            enabled=request.enabled,
        )

    def list_servers(self) -> tuple[McpServerSummary, ...]:
        return (
            McpServerSummary(
                name="filesystem",
                source=McpConfigScope.APP,
                transport="stdio",
            ),
        )

    async def test_server_connection(self, name: str) -> McpServerConnectionTestResult:
        if name != "filesystem":
            raise ValueError(f"Unknown MCP server: {name}")
        return McpServerConnectionTestResult(
            server="filesystem",
            source=McpConfigScope.APP,
            transport="stdio",
            ok=True,
            tool_count=1,
            tools=(
                McpToolInfo(name="filesystem_read_file", description="Read a file"),
            ),
        )

    async def list_server_tools(self, name: str) -> McpServerToolsSummary:
        if name != "filesystem":
            raise ValueError(f"Unknown MCP server: {name}")
        return McpServerToolsSummary(
            server="filesystem",
            source=McpConfigScope.APP,
            transport="stdio",
            tools=(
                McpToolInfo(name="filesystem_read_file", description="Read a file"),
            ),
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
            "source": "app",
            "transport": "stdio",
            "enabled": True,
        }
    ]


def test_add_mcp_server() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.post(
        "/api/mcp/servers",
        json={
            "name": "filesystem",
            "config": {"transport": "stdio", "command": "npx"},
            "overwrite": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "server": {
            "name": "filesystem",
            "source": "app",
            "transport": "stdio",
            "enabled": True,
        },
        "config_path": "C:/Users/test/.relay-teams/mcp.json",
    }


def test_add_mcp_server_returns_400_for_invalid_request() -> None:
    class _InvalidAddService(_FakeMcpService):
        def add_server(
            self,
            *,
            name: str,
            server_config: dict[str, object],
            overwrite: bool = False,
        ) -> McpServerAddResult:
            _ = name, server_config, overwrite
            raise ValueError("MCP server already exists: filesystem")

    client = _create_test_client(_InvalidAddService())

    response = client.post(
        "/api/mcp/servers",
        json={
            "name": "filesystem",
            "config": {"transport": "stdio", "command": "npx"},
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "MCP server already exists: filesystem"}


def test_add_mcp_server_returns_503_when_config_manager_is_unavailable() -> None:
    class _UnavailableAddService(_FakeMcpService):
        def add_server(
            self,
            *,
            name: str,
            server_config: dict[str, object],
            overwrite: bool = False,
        ) -> McpServerAddResult:
            _ = name, server_config, overwrite
            raise RuntimeError("MCP config manager is not available")

    client = _create_test_client(_UnavailableAddService())

    response = client.post(
        "/api/mcp/servers",
        json={
            "name": "filesystem",
            "config": {"transport": "stdio", "command": "npx"},
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "MCP config manager is not available"}


def test_set_mcp_server_enabled() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.put(
        "/api/mcp/servers/filesystem/enabled", json={"enabled": False}
    )

    assert response.status_code == 200
    assert response.json() == {
        "name": "filesystem",
        "source": "app",
        "transport": "stdio",
        "enabled": False,
    }


def test_set_mcp_server_enabled_returns_503_when_unavailable() -> None:
    class _UnavailableEnableService(_FakeMcpService):
        def set_server_enabled(
            self,
            name: str,
            request: McpServerEnabledUpdateRequest,
        ) -> McpServerSummary:
            _ = name, request
            raise RuntimeError("MCP config manager is not available")

    client = _create_test_client(_UnavailableEnableService())

    response = client.put(
        "/api/mcp/servers/filesystem/enabled", json={"enabled": False}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "MCP config manager is not available"}


def test_set_mcp_server_enabled_returns_404_for_unknown_server() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.put("/api/mcp/servers/missing/enabled", json={"enabled": True})

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown MCP server: missing"}


def test_get_mcp_server_config() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.get("/api/mcp/servers/filesystem")

    assert response.status_code == 200
    assert response.json() == {
        "server": {
            "name": "filesystem",
            "source": "app",
            "transport": "stdio",
            "enabled": True,
        },
        "config": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        },
    }


def test_get_mcp_server_config_returns_404_for_unknown_server() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.get("/api/mcp/servers/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown MCP server: missing"}


def test_get_mcp_server_config_returns_503_when_unavailable() -> None:
    class _UnavailableGetService(_FakeMcpService):
        def get_server_config(self, name: str) -> McpServerConfigResult:
            _ = name
            raise RuntimeError("MCP config manager is not available")

    client = _create_test_client(_UnavailableGetService())

    response = client.get("/api/mcp/servers/filesystem")

    assert response.status_code == 503
    assert response.json() == {"detail": "MCP config manager is not available"}


def test_update_mcp_server_config() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.put(
        "/api/mcp/servers/filesystem",
        json={"config": {"transport": "stdio", "command": "uvx"}},
    )

    assert response.status_code == 200
    assert response.json()["config"] == {"transport": "stdio", "command": "uvx"}


def test_update_mcp_server_config_returns_404_for_unknown_server() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.put(
        "/api/mcp/servers/missing",
        json={"config": {"transport": "stdio", "command": "uvx"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown MCP server: missing"}


def test_update_mcp_server_config_returns_503_when_unavailable() -> None:
    class _UnavailableUpdateService(_FakeMcpService):
        def update_server(
            self,
            name: str,
            request: McpServerUpdateRequest,
        ) -> McpServerConfigResult:
            _ = name, request
            raise RuntimeError("MCP config manager is not available")

    client = _create_test_client(_UnavailableUpdateService())

    response = client.put(
        "/api/mcp/servers/filesystem",
        json={"config": {"transport": "stdio", "command": "uvx"}},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "MCP config manager is not available"}


def test_list_mcp_server_tools() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.get("/api/mcp/servers/filesystem/tools")

    assert response.status_code == 200
    assert response.json() == {
        "server": "filesystem",
        "source": "app",
        "transport": "stdio",
        "enabled": True,
        "tools": [{"name": "filesystem_read_file", "description": "Read a file"}],
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


def test_test_mcp_server_connection() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.post("/api/mcp/servers/filesystem/test")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["tool_count"] == 1


def test_test_mcp_server_connection_returns_404_for_unknown_server() -> None:
    client = _create_test_client(_FakeMcpService())

    response = client.post("/api/mcp/servers/missing/test")

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown MCP server: missing"}
