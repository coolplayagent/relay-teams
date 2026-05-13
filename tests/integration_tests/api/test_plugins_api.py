from __future__ import annotations

import json
from pathlib import Path
import subprocess
from uuid import uuid4

import httpx

from integration_tests.support.environment import IntegrationEnvironment


def test_plugin_config_api_roundtrip(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
    tmp_path: Path,
) -> None:
    plugin_name = f"quality-{uuid4().hex[:8]}"
    plugin_root = tmp_path / plugin_name
    _write_plugin_manifest(plugin_root, name=plugin_name)
    (plugin_root / "mcp.json").write_text(
        json.dumps(
            {
                "docs": {
                    "command": "${user_config.endpoint}",
                    "args": ["${user_config.token}"],
                }
            }
        ),
        encoding="utf-8",
    )

    validate_response = api_client.post(
        "/api/system/configs/plugins:validate",
        json={"path": str(plugin_root)},
    )
    validate_response.raise_for_status()
    assert validate_response.json()["plugins"][0]["name"] == plugin_name

    install_response = api_client.post(
        "/api/system/configs/plugins:install",
        json={"source": str(plugin_root), "scope": "user"},
    )
    install_response.raise_for_status()
    installed_payload = install_response.json()
    assert installed_payload["plugins"][0]["name"] == plugin_name
    assert installed_payload["plugins"][0]["enabled"] is False
    assert (
        "Missing required plugin user_config field(s): token"
        in (installed_payload["diagnostics"][0]["message"])
    )

    configure_response = api_client.post(
        f"/api/system/configs/plugins/{plugin_name}:configure",
        json={
            "scope": "user",
            "user_config": {
                "endpoint": "echo",
                "token": "integration-secret",
            },
        },
    )
    configure_response.raise_for_status()
    configured_payload = configure_response.json()
    assert all(
        diagnostic["severity"] != "error"
        for diagnostic in configured_payload["diagnostics"]
    )
    plugin_payload = configured_payload["plugins"][0]
    assert plugin_payload["component_counts"]["mcp_servers"] == 1
    assert plugin_payload["user_config"] == {
        "endpoint": "echo",
        "token": "<configured>",
    }
    assert any(
        "Plugin MCP command" in diagnostic["message"]
        and "integration-secret" not in diagnostic["message"]
        for diagnostic in configured_payload["diagnostics"]
    )

    state_file = integration_env.config_dir / "plugins" / "plugins.json"
    assert "integration-secret" not in state_file.read_text(encoding="utf-8")

    disable_response = api_client.post(
        f"/api/system/configs/plugins/{plugin_name}:disable",
        json={"scope": "user"},
    )
    disable_response.raise_for_status()
    assert disable_response.json()["plugins"][0]["enabled"] is False

    enable_response = api_client.post(
        f"/api/system/configs/plugins/{plugin_name}:enable",
        json={"scope": "user"},
    )
    enable_response.raise_for_status()
    assert enable_response.json()["plugins"][0]["enabled"] is True

    list_response = api_client.get("/api/system/configs/plugins")
    list_response.raise_for_status()
    assert any(
        plugin["name"] == plugin_name for plugin in list_response.json()["plugins"]
    )

    delete_response = api_client.delete(
        f"/api/system/configs/plugins/{plugin_name}?scope=user&prune=true",
    )
    delete_response.raise_for_status()
    assert all(
        plugin["name"] != plugin_name for plugin in delete_response.json()["plugins"]
    )


def test_plugin_marketplace_api_lists_versions(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    marketplace_path = tmp_path / "marketplace.json"
    marketplace_path.write_text(
        json.dumps(
            {
                "version": "1",
                "plugins": [
                    {
                        "name": "quality",
                        "description": "Quality tools",
                        "latest": "2.0.0",
                        "versions": [
                            {
                                "version": "1.0.0",
                                "source": {
                                    "kind": "local",
                                    "value": str(tmp_path / "quality-v1"),
                                },
                            },
                            {
                                "version": "2.0.0",
                                "source": {
                                    "kind": "git",
                                    "value": "https://example.invalid/quality.git",
                                },
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    response = api_client.post(
        "/api/system/configs/plugins/marketplace",
        json={"marketplace": str(marketplace_path)},
    )

    response.raise_for_status()
    payload = response.json()
    assert payload["plugins"][0]["name"] == "quality"
    assert payload["plugins"][0]["latest"] == "2.0.0"
    assert [item["version"] for item in payload["plugins"][0]["versions"]] == [
        "1.0.0",
        "2.0.0",
    ]


def test_plugin_marketplace_api_searches_local_index(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    marketplace_path = tmp_path / "marketplace.json"
    marketplace_path.write_text(
        json.dumps(
            {
                "version": "1",
                "plugins": [
                    {
                        "name": "quality",
                        "description": "Quality tools",
                        "latest": "1.0.0",
                    },
                    {
                        "name": "market",
                        "description": "Market data",
                        "latest": "1.0.0",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    response = api_client.post(
        "/api/system/configs/plugins/marketplace:search",
        json={"marketplace": str(marketplace_path), "query": "quality"},
    )

    response.raise_for_status()
    payload = response.json()
    assert [plugin["name"] for plugin in payload["plugins"]] == ["quality"]


def test_plugin_install_api_accepts_local_git_and_marketplace_sources(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    local_name = f"local-{uuid4().hex[:8]}"
    git_name = f"git-{uuid4().hex[:8]}"
    marketplace_name = f"market-{uuid4().hex[:8]}"
    local_root = tmp_path / local_name
    git_root = tmp_path / git_name
    marketplace_root = tmp_path / marketplace_name
    _write_runtime_plugin(local_root, name=local_name)
    _write_runtime_plugin(git_root, name=git_name)
    _write_runtime_plugin(marketplace_root, name=marketplace_name)
    _init_git_repo(git_root)
    marketplace_path = tmp_path / "marketplace.json"
    marketplace_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": marketplace_name,
                        "latest": "1.0.0",
                        "versions": [
                            {
                                "version": "1.0.0",
                                "source": {
                                    "kind": "local",
                                    "value": str(marketplace_root),
                                },
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    install_cases = [
        {
            "source": str(local_root),
            "scope": "user",
            "enabled": True,
            "source_kind": "local",
        },
        {
            "source": str(git_root),
            "scope": "user",
            "enabled": True,
            "source_kind": "git",
            "source_ref": "main",
        },
        {
            "source": marketplace_name,
            "scope": "user",
            "enabled": True,
            "marketplace": str(marketplace_path),
            "version": "1.0.0",
        },
    ]

    for payload in install_cases:
        response = api_client.post(
            "/api/system/configs/plugins:install",
            json=payload,
        )
        response.raise_for_status()

    runtime_response = api_client.get("/api/system/configs/plugins/runtime")
    runtime_response.raise_for_status()
    plugins = {plugin["name"]: plugin for plugin in runtime_response.json()["plugins"]}
    for name in (local_name, git_name, marketplace_name):
        assert plugins[name]["enabled"] is True
        assert plugins[name]["component_counts"]["commands"] == 1
    assert plugins[git_name]["source"]["kind"] == "git"
    assert plugins[git_name]["source"]["ref"] == "main"
    assert plugins[marketplace_name]["source"]["kind"] == "marketplace"
    assert plugins[marketplace_name]["source"]["marketplace"] == str(
        marketplace_path.resolve()
    )


def _write_plugin_manifest(plugin_root: Path, *, name: str) -> None:
    manifest_dir = plugin_root / ".relay-teams"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "mcpServers": "./mcp.json",
                "userConfig": {
                    "endpoint": {
                        "type": "string",
                        "default": "echo",
                    },
                    "token": {
                        "type": "string",
                        "required": True,
                        "sensitive": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _write_runtime_plugin(plugin_root: Path, *, name: str) -> None:
    manifest_dir = plugin_root / ".relay-teams"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0"}),
        encoding="utf-8",
    )
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        "---\ndescription: Review code\n---\n\nReview $ARGUMENTS\n",
        encoding="utf-8",
    )


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial plugin"],
        cwd=path,
        check=True,
    )
