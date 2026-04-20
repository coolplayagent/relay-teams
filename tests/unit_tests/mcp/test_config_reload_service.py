# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

from relay_teams.mcp import (
    McpConfigManager,
    McpConfigReloadService,
)
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry


def test_reload_mcp_config_ignores_unknown_servers_on_existing_roles(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="writer",
            name="Writer",
            description="Writes documents.",
            version="1.0.0",
            tools=(),
            mcp_servers=("missing_server",),
            skills=(),
            model_profile="default",
            system_prompt="Write clearly.",
        )
    )
    reloaded_registries = []
    service = McpConfigReloadService(
        mcp_config_manager=McpConfigManager(app_config_dir=app_config_dir),
        role_registry=role_registry,
        on_mcp_reloaded=lambda registry: reloaded_registries.append(registry),
    )

    service.reload_mcp_config()

    assert len(reloaded_registries) == 1
    assert reloaded_registries[0].list_names() == ()


def test_reload_mcp_config_cleans_uvx_package_cache_before_reload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "uvx"}}},
                server_config={
                    "command": "uvx",
                    "args": ["mcp-server-filesystem"],
                },
                source=McpConfigScope.APP,
            ),
        )
    )
    manager = McpConfigManager(app_config_dir=app_config_dir)
    monkeypatch.setattr(manager, "load_registry", lambda: registry)
    recorded_commands: list[tuple[str, ...]] = []

    def _fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        recorded_commands.append(tuple(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(
        "relay_teams.mcp.config_reload_service.subprocess.run", _fake_run
    )
    service = McpConfigReloadService(
        mcp_config_manager=manager,
        role_registry=role_registry,
        on_mcp_reloaded=lambda _registry: None,
    )

    service.reload_mcp_config()

    assert recorded_commands == [
        ("uv", "cache", "clean", "--force", "mcp-server-filesystem")
    ]


def test_reload_mcp_config_cleans_uv_tool_run_package_from_from_flag(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "uv"}}},
                server_config={
                    "command": "uv",
                    "args": [
                        "tool",
                        "run",
                        "--from=context7-mcp",
                        "context7",
                    ],
                },
                source=McpConfigScope.APP,
            ),
        )
    )
    manager = McpConfigManager(app_config_dir=app_config_dir)
    monkeypatch.setattr(manager, "load_registry", lambda: registry)
    recorded_commands: list[tuple[str, ...]] = []

    def _fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        recorded_commands.append(tuple(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(
        "relay_teams.mcp.config_reload_service.subprocess.run", _fake_run
    )
    service = McpConfigReloadService(
        mcp_config_manager=manager,
        role_registry=role_registry,
        on_mcp_reloaded=lambda _registry: None,
    )

    service.reload_mcp_config()

    assert recorded_commands == [("uv", "cache", "clean", "--force", "context7-mcp")]


def test_reload_mcp_config_falls_back_to_global_uv_cache_clean_when_package_unknown(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "uvx"}}},
                server_config={
                    "command": "uvx",
                    "args": ["--isolated", "--env-file", ".env"],
                },
                source=McpConfigScope.APP,
            ),
        )
    )
    manager = McpConfigManager(app_config_dir=app_config_dir)
    monkeypatch.setattr(manager, "load_registry", lambda: registry)
    recorded_commands: list[tuple[str, ...]] = []

    def _fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        recorded_commands.append(tuple(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(
        "relay_teams.mcp.config_reload_service.subprocess.run", _fake_run
    )
    service = McpConfigReloadService(
        mcp_config_manager=manager,
        role_registry=role_registry,
        on_mcp_reloaded=lambda _registry: None,
    )

    service.reload_mcp_config()

    assert recorded_commands == [("uv", "cache", "clean", "--force")]


def test_reload_mcp_config_skips_uv_cache_clean_for_uv_run_projects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "uv"}}},
                server_config={
                    "command": "uv",
                    "args": ["run", "python", "-m", "my_mcp_server"],
                },
                source=McpConfigScope.APP,
            ),
        )
    )
    manager = McpConfigManager(app_config_dir=app_config_dir)
    monkeypatch.setattr(manager, "load_registry", lambda: registry)
    recorded_commands: list[tuple[str, ...]] = []

    def _fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        recorded_commands.append(tuple(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(
        "relay_teams.mcp.config_reload_service.subprocess.run", _fake_run
    )
    service = McpConfigReloadService(
        mcp_config_manager=manager,
        role_registry=role_registry,
        on_mcp_reloaded=lambda _registry: None,
    )

    service.reload_mcp_config()

    assert recorded_commands == []


def test_reload_mcp_config_retries_without_force_for_legacy_uv(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "uvx"}}},
                server_config={
                    "command": "uvx",
                    "args": ["mcp-server-filesystem"],
                },
                source=McpConfigScope.APP,
            ),
        )
    )
    manager = McpConfigManager(app_config_dir=app_config_dir)
    monkeypatch.setattr(manager, "load_registry", lambda: registry)
    recorded_commands: list[tuple[str, ...]] = []

    def _fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        argv = tuple(args[0])
        recorded_commands.append(argv)
        if argv == ("uv", "cache", "clean", "--force", "mcp-server-filesystem"):
            return subprocess.CompletedProcess(
                args[0],
                2,
                stdout="",
                stderr="error: unexpected argument '--force' found",
            )
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(
        "relay_teams.mcp.config_reload_service.subprocess.run", _fake_run
    )
    service = McpConfigReloadService(
        mcp_config_manager=manager,
        role_registry=role_registry,
        on_mcp_reloaded=lambda _registry: None,
    )

    service.reload_mcp_config()

    assert recorded_commands == [
        ("uv", "cache", "clean", "--force", "mcp-server-filesystem"),
        ("uv", "cache", "clean", "mcp-server-filesystem"),
    ]


def test_reload_mcp_config_reuses_configured_uv_path_for_uvx_servers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "/custom/bin/uvx"}}},
                server_config={
                    "command": "/custom/bin/uvx",
                    "args": ["mcp-server-filesystem"],
                },
                source=McpConfigScope.APP,
            ),
        )
    )
    manager = McpConfigManager(app_config_dir=app_config_dir)
    monkeypatch.setattr(manager, "load_registry", lambda: registry)
    recorded_commands: list[tuple[str, ...]] = []

    def _fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        recorded_commands.append(tuple(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(
        "relay_teams.mcp.config_reload_service.subprocess.run", _fake_run
    )
    service = McpConfigReloadService(
        mcp_config_manager=manager,
        role_registry=role_registry,
        on_mcp_reloaded=lambda _registry: None,
    )

    service.reload_mcp_config()

    assert recorded_commands == [
        ("/custom/bin/uv", "cache", "clean", "--force", "mcp-server-filesystem")
    ]


def test_reload_mcp_config_parses_uv_global_options_before_tool_run(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "/custom/bin/uv"}}},
                server_config={
                    "command": "/custom/bin/uv",
                    "args": [
                        "--project",
                        "/repo",
                        "--color=always",
                        "tool",
                        "run",
                        "--from=context7-mcp",
                        "context7",
                    ],
                },
                source=McpConfigScope.APP,
            ),
        )
    )
    manager = McpConfigManager(app_config_dir=app_config_dir)
    monkeypatch.setattr(manager, "load_registry", lambda: registry)
    recorded_commands: list[tuple[str, ...]] = []

    def _fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        recorded_commands.append(tuple(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(
        "relay_teams.mcp.config_reload_service.subprocess.run", _fake_run
    )
    service = McpConfigReloadService(
        mcp_config_manager=manager,
        role_registry=role_registry,
        on_mcp_reloaded=lambda _registry: None,
    )

    service.reload_mcp_config()

    assert recorded_commands == [
        ("/custom/bin/uv", "cache", "clean", "--force", "context7-mcp")
    ]


def test_reload_mcp_config_parses_uv_cache_dir_before_tool_run(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "uv"}}},
                server_config={
                    "command": "uv",
                    "args": [
                        "--cache-dir",
                        "/tmp/uv-cache",
                        "tool",
                        "run",
                        "--from=context7-mcp",
                        "context7",
                    ],
                },
                source=McpConfigScope.APP,
            ),
        )
    )
    manager = McpConfigManager(app_config_dir=app_config_dir)
    monkeypatch.setattr(manager, "load_registry", lambda: registry)
    recorded_commands: list[tuple[str, ...]] = []

    def _fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        recorded_commands.append(tuple(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(
        "relay_teams.mcp.config_reload_service.subprocess.run", _fake_run
    )
    service = McpConfigReloadService(
        mcp_config_manager=manager,
        role_registry=role_registry,
        on_mcp_reloaded=lambda _registry: None,
    )

    service.reload_mcp_config()

    assert recorded_commands == [("uv", "cache", "clean", "--force", "context7-mcp")]
