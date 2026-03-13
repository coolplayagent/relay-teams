# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

from agent_teams.mcp import config_manager
from agent_teams.mcp.models import McpConfigScope


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
    ):
        monkeypatch.delenv(key, raising=False)


def test_load_registry_reads_app_scope_only(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".config" / "agent-teams"
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
    app_config_dir = tmp_path / ".config" / "agent-teams"
    app_config_dir.mkdir(parents=True)

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
        "SSL_VERIFY": "true",
    }
    assert registry.get_spec("filesystem").server_config["env"] == expected_proxy_env
    assert registry.get_spec("events").server_config["env"] == expected_proxy_env
    assert registry.get_spec("api").server_config["env"] == expected_proxy_env
    assert os.environ["HTTP_PROXY"] == "http://proxy.internal:8080"
    assert os.environ["http_proxy"] == "http://proxy.internal:8080"
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1"
    assert os.environ["no_proxy"] == "localhost,127.0.0.1"


def test_load_registry_preserves_explicit_server_env_over_proxy_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_proxy_env(monkeypatch)
    app_config_dir = tmp_path / ".config" / "agent-teams"
    app_config_dir.mkdir(parents=True)
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

    assert registry.get_spec("remote").server_config["env"] == {
        "HTTP_PROXY": "http://custom-proxy.internal:9000",
        "http_proxy": "http://custom-proxy.internal:9000",
        "CUSTOM_TOKEN": "secret",
        "SSL_VERIFY": "true",
    }


def test_load_registry_accepts_utf8_bom(tmp_path: Path) -> None:
    app_config_dir = tmp_path / ".config" / "agent-teams"
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


def test_get_mcp_file_paths_follow_scope_conventions(
    monkeypatch,
) -> None:
    app_config_dir = Path("D:/home/.config/agent-teams").resolve()
    monkeypatch.setattr(
        config_manager,
        "get_app_config_dir",
        lambda **kwargs: app_config_dir,
    )

    assert config_manager.get_project_mcp_file_path() == app_config_dir / "mcp.json"
    assert config_manager.get_user_mcp_file_path() == app_config_dir / "mcp.json"
