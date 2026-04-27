# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic_ai.mcp import MCPServerStdio

import relay_teams.mcp.mcp_config_manager as config_manager
from relay_teams.mcp.mcp_models import McpConfigScope
from relay_teams.mcp.mcp_registry import build_mcp_server


def _clear_proxy_env(monkeypatch) -> None:
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
        "SSL_VERIFY",
        "NODE_USE_ENV_PROXY",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "NPM_CONFIG_PROXY",
        "npm_config_proxy",
        "NPM_CONFIG_HTTPS_PROXY",
        "npm_config_https_proxy",
        "NPM_CONFIG_NOPROXY",
        "npm_config_noproxy",
        "NPM_CONFIG_STRICT_SSL",
        "npm_config_strict_ssl",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_test_app_config_dir(monkeypatch, config_dir: Path) -> None:
    monkeypatch.setattr(
        "relay_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )


def test_load_registry_reads_app_scope_only(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    (app_config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"command": "app-shared"},
                    "app_only": {
                        "transport": "streamable-http",
                        "url": "https://example.com/mcp",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    registry = manager.load_registry()
    specs = registry.list_specs()

    assert [spec.name for spec in specs] == ["app_only", "shared"]
    assert registry.get_spec("shared").source == McpConfigScope.APP
    assert registry.get_spec("shared").server_config["command"] == "app-shared"


def test_load_registry_applies_proxy_env_to_all_mcp_server_configs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_proxy_env(monkeypatch)
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    _set_test_app_config_dir(monkeypatch, app_config_dir)

    (app_config_dir / ".env").write_text(
        "HTTP_PROXY=http://proxy.internal:8080\nNO_PROXY=localhost,127.0.0.1\n",
        encoding="utf-8",
    )
    (app_config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "uvx",
                        "args": ["mcp-server-filesystem"],
                    },
                    "events": {
                        "transport": "sse",
                        "url": "https://example.com/sse",
                    },
                    "api": {
                        "transport": "http",
                        "url": "https://example.com/mcp",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    registry = manager.load_registry()

    expected_proxy_env = {
        "HTTP_PROXY": "http://proxy.internal:8080",
        "http_proxy": "http://proxy.internal:8080",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
        "NODE_USE_ENV_PROXY": "1",
        "NPM_CONFIG_PROXY": "http://proxy.internal:8080",
        "npm_config_proxy": "http://proxy.internal:8080",
        "NPM_CONFIG_HTTPS_PROXY": "http://proxy.internal:8080",
        "npm_config_https_proxy": "http://proxy.internal:8080",
        "NPM_CONFIG_NOPROXY": "localhost,127.0.0.1",
        "npm_config_noproxy": "localhost,127.0.0.1",
    }
    assert registry.get_spec("filesystem").server_config["env"] == expected_proxy_env
    assert registry.get_spec("events").server_config["env"] == expected_proxy_env
    assert registry.get_spec("api").server_config["env"] == expected_proxy_env
    assert os.environ["HTTP_PROXY"] == "http://proxy.internal:8080"
    assert os.environ["http_proxy"] == "http://proxy.internal:8080"
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1"
    assert os.environ["no_proxy"] == "localhost,127.0.0.1"
    assert os.environ["NODE_USE_ENV_PROXY"] == "1"
    assert os.environ["npm_config_proxy"] == "http://proxy.internal:8080"
    assert os.environ["npm_config_https_proxy"] == "http://proxy.internal:8080"
    assert os.environ["npm_config_noproxy"] == "localhost,127.0.0.1"


def test_load_registry_preserves_explicit_server_env_over_proxy_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_proxy_env(monkeypatch)
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    _set_test_app_config_dir(monkeypatch, app_config_dir)
    (app_config_dir / ".env").write_text(
        "HTTP_PROXY=http://proxy.internal:8080\n",
        encoding="utf-8",
    )
    (app_config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "env": {
                            "HTTP_PROXY": "http://custom-proxy.internal:9000",
                            "CUSTOM_TOKEN": "secret",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    registry = manager.load_registry()
    server_env = registry.get_spec("remote").server_config["env"]

    assert isinstance(server_env, dict)
    assert server_env["HTTP_PROXY"] == "http://custom-proxy.internal:9000"
    assert server_env["http_proxy"] == "http://custom-proxy.internal:9000"
    assert server_env["NODE_USE_ENV_PROXY"] == "1"
    assert server_env["NPM_CONFIG_PROXY"] == "http://custom-proxy.internal:9000"
    assert server_env["npm_config_proxy"] == "http://custom-proxy.internal:9000"
    assert server_env["NPM_CONFIG_HTTPS_PROXY"] == "http://custom-proxy.internal:9000"
    assert server_env["npm_config_https_proxy"] == "http://custom-proxy.internal:9000"
    assert server_env["CUSTOM_TOKEN"] == "secret"


def test_load_registry_accepts_utf8_bom(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    content = json.dumps(
        {
            "mcpServers": {
                "time-mcp": {
                    "command": "npx",
                    "args": ["-y", "time-mcp"],
                }
            }
        },
        indent=2,
    )
    (app_config_dir / "mcp.json").write_text(content, encoding="utf-8-sig")

    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    registry = manager.load_registry()

    assert registry.list_names() == ("time-mcp",)


def test_add_server_writes_app_mcp_config_and_reload_discovers_it(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    config_path = manager.add_server(
        name="filesystem",
        server_config={
            "type": "local",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        },
    )

    assert config_path == app_config_dir / "mcp.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["filesystem"] == {
        "type": "local",
        "command": "npx",
        "transport": "stdio",
        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
    }
    registry = manager.load_registry()
    assert registry.get_spec("filesystem").server_config["command"] == "npx"


def test_add_server_rejects_duplicate_without_overwrite(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    (app_config_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"filesystem": {"command": "npx"}}}),
        encoding="utf-8",
    )
    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    try:
        manager.add_server(name="filesystem", server_config={"command": "uvx"})
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Expected duplicate MCP server to be rejected")


def test_set_server_enabled_updates_app_mcp_config(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    config_path = app_config_dir / "mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"filesystem": {"command": "npx"}}}),
        encoding="utf-8",
    )
    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    manager.set_server_enabled(name="filesystem", enabled=False)

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["filesystem"]["enabled"] is False
    registry = manager.load_registry()
    spec = registry.get_spec("filesystem")
    assert spec.enabled is False
    assert registry.list_names() == ("filesystem",)
    assert registry.list_enabled_names() == ()


def test_update_server_preserves_enabled_state(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    config_path = app_config_dir / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "transport": "stdio",
                        "command": "npx",
                        "enabled": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    manager.update_server(
        name="filesystem",
        server_config={
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-filesystem"],
        },
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["filesystem"] == {
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-filesystem"],
        "enabled": False,
    }
    assert manager.get_server_config("filesystem")["command"] == "uvx"


def test_load_registry_accepts_disabled_alias(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    (app_config_dir / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"filesystem": {"command": "npx", "disabled": True}}}
        ),
        encoding="utf-8",
    )
    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    registry = manager.load_registry()

    assert registry.get_spec("filesystem").enabled is False


def test_load_registry_normalizes_opencode_remote_type(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    (app_config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote-docs": {
                        "type": "remote",
                        "url": "https://example.com/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    registry = manager.load_registry()

    assert registry.get_spec("remote-docs").server_config["transport"] == "http"


def test_load_registry_syncs_app_environment_for_stdio_mcp(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    env_key = "MCP_SYNCED_APP_ENV"
    (app_config_dir / ".env").write_text(f"{env_key}=from-app\n", encoding="utf-8")
    (app_config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "time-mcp": {
                        "command": "npx",
                        "args": ["-y", "@upstash/context7-mcp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = config_manager.McpConfigManager(app_config_dir=app_config_dir)

    registry = manager.load_registry()
    server = build_mcp_server(registry.get_spec("time-mcp"))

    assert isinstance(server, MCPServerStdio)
    assert os.environ[env_key] == "from-app"
    assert server.env is not None
    assert server.env[env_key] == "from-app"


def test_get_mcp_file_paths_follow_scope_conventions(
    monkeypatch,
) -> None:
    app_config_dir = Path("D:/home/.agent-teams").resolve()
    monkeypatch.setattr(
        config_manager,
        "get_app_config_dir",
        lambda **kwargs: app_config_dir,
    )

    assert config_manager.get_project_mcp_file_path() == app_config_dir / "mcp.json"
    assert config_manager.get_user_mcp_file_path() == app_config_dir / "mcp.json"
