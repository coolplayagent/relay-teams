# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue
import pytest

from relay_teams.interfaces.server.deps import get_container
from relay_teams.interfaces.server.routers import system
from relay_teams.plugins import (
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginManifest,
    PluginRecord,
    PluginRegistry,
    PluginScope,
    PluginStateRecord,
)
from relay_teams.plugins.plugin_models import (
    PluginInstallSource,
    PluginInstallSourceKind,
)
from relay_teams.plugins.marketplace_models import PluginMarketplaceProviderKind


class _FakePluginConfigManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.installed: tuple[str, PluginScope, bool] | None = None
        self.git_installed: tuple[str, PluginScope, str, bool] | None = None
        self.marketplace_installed: (
            tuple[
                str,
                str,
                PluginScope,
                str | None,
                bool,
                PluginMarketplaceProviderKind | str,
                str,
                str,
            ]
            | None
        ) = None
        self.uninstalled: tuple[str, PluginScope, bool] | None = None
        self.enabled: tuple[str, PluginScope, bool] | None = None
        self.updated: tuple[str, PluginScope, str | None] | None = None
        self.configured: tuple[str, PluginScope, dict[str, JsonValue]] | None = None
        self.marketplace_error: OSError | None = None

    def load_registry(self) -> PluginRegistry:
        return PluginRegistry(plugins=(_plugin_record(self.root_dir),))

    def install_plugin(
        self,
        *,
        source: Path,
        scope: PluginScope,
        enabled: bool = True,
    ) -> PluginStateRecord:
        self.installed = (str(source), scope, enabled)
        return _plugin_state_record(self.root_dir, scope=scope, enabled=enabled)

    def install_git_plugin(
        self,
        *,
        source: str,
        scope: PluginScope,
        ref: str = "",
        enabled: bool = True,
    ) -> PluginStateRecord:
        self.git_installed = (source, scope, ref, enabled)
        return _plugin_state_record(self.root_dir, scope=scope, enabled=enabled)

    def install_marketplace_plugin(
        self,
        *,
        name: str,
        marketplace: Path,
        scope: PluginScope,
        version: str | None = None,
        enabled: bool = True,
        marketplace_provider: PluginMarketplaceProviderKind | str = "local_json",
        marketplace_source: str = "",
        marketplace_ref: str = "",
    ) -> PluginStateRecord:
        if self.marketplace_error is not None:
            raise self.marketplace_error
        self.marketplace_installed = (
            name,
            str(marketplace),
            scope,
            version,
            enabled,
            marketplace_provider,
            marketplace_source,
            marketplace_ref,
        )
        return _plugin_state_record(self.root_dir, scope=scope, enabled=enabled)

    def validate_plugin(
        self,
        *,
        plugin_root: Path,
        require_manifest: bool = False,
        strict_explicit_paths: bool = False,
    ) -> tuple[PluginRecord, tuple[PluginDiagnostic, ...]]:
        _ = require_manifest
        _ = strict_explicit_paths
        return (
            _plugin_record(plugin_root),
            (
                PluginDiagnostic(
                    plugin_name="quality",
                    scope=PluginScope.LOCAL,
                    severity=PluginDiagnosticSeverity.WARNING,
                    message="validated",
                ),
            ),
        )

    def uninstall_plugin(
        self,
        *,
        name: str,
        scope: PluginScope,
        prune: bool = False,
    ) -> PluginStateRecord:
        self.uninstalled = (name, scope, prune)
        return _plugin_state_record(self.root_dir, scope=scope, enabled=True)

    def set_plugin_enabled(
        self,
        *,
        name: str,
        scope: PluginScope,
        enabled: bool,
    ) -> PluginStateRecord:
        self.enabled = (name, scope, enabled)
        return _plugin_state_record(self.root_dir, scope=scope, enabled=enabled)

    def update_plugin(
        self,
        *,
        name: str,
        scope: PluginScope,
        version: str | None = None,
    ) -> PluginStateRecord:
        self.updated = (name, scope, version)
        return _plugin_state_record(self.root_dir, scope=scope, enabled=True)

    def set_plugin_user_config(
        self,
        *,
        name: str,
        scope: PluginScope,
        user_config: dict[str, JsonValue],
    ) -> PluginStateRecord:
        self.configured = (name, scope, user_config)
        return _plugin_state_record(self.root_dir, scope=scope, enabled=True)


class _FakeContainer:
    def __init__(self, root_dir: Path) -> None:
        self.config_dir = root_dir.parent
        self.plugin_config_manager = _FakePluginConfigManager(root_dir)
        self.plugin_registry = PluginRegistry()
        self.reload_count = 0

    def reload_plugin_runtime(self) -> None:
        self.reload_count += 1
        self.plugin_registry = self.plugin_config_manager.load_registry()


def test_plugin_install_api_reloads_runtime(tmp_path: Path) -> None:
    container = _FakeContainer(tmp_path / "quality")
    client = _create_client(container)

    response = client.post(
        "/api/system/configs/plugins:install",
        json={"source": str(tmp_path / "source"), "scope": "user", "enabled": True},
    )

    assert response.status_code == 200
    assert container.plugin_config_manager.installed == (
        str(tmp_path / "source"),
        PluginScope.USER,
        True,
    )
    assert container.reload_count == 1
    assert response.json()["plugins"][0]["name"] == "quality"


def test_plugin_install_api_supports_git_and_marketplace_sources(
    tmp_path: Path,
) -> None:
    container = _FakeContainer(tmp_path / "quality")
    client = _create_client(container)

    git_response = client.post(
        "/api/system/configs/plugins:install",
        json={
            "source": "https://example.test/plugin.git",
            "source_kind": "git",
            "source_ref": "v1",
            "scope": "user",
            "enabled": False,
        },
    )
    marketplace_response = client.post(
        "/api/system/configs/plugins:install",
        json={
            "source": "quality",
            "marketplace": str(tmp_path / "marketplace.json"),
            "scope": "project",
            "version": "2.0.0",
            "enabled": True,
        },
    )

    assert git_response.status_code == 200
    assert marketplace_response.status_code == 200
    assert container.plugin_config_manager.git_installed == (
        "https://example.test/plugin.git",
        PluginScope.USER,
        "v1",
        False,
    )
    assert container.plugin_config_manager.marketplace_installed == (
        "quality",
        str(tmp_path / "marketplace.json"),
        PluginScope.PROJECT,
        "2.0.0",
        True,
        PluginMarketplaceProviderKind.LOCAL_JSON,
        "",
        "",
    )
    assert container.reload_count == 2


def test_plugin_install_api_rejects_marketplace_kind_without_marketplace(
    tmp_path: Path,
) -> None:
    container = _FakeContainer(tmp_path / "quality")
    client = _create_client(container)

    response = client.post(
        "/api/system/configs/plugins:install",
        json={
            "source": "quality",
            "source_kind": "marketplace",
            "scope": "user",
        },
    )

    assert response.status_code == 400
    assert "Marketplace plugin installs require marketplace" in response.text


def test_plugin_install_api_maps_marketplace_filesystem_error_to_400(
    tmp_path: Path,
) -> None:
    container = _FakeContainer(tmp_path / "quality")
    container.plugin_config_manager.marketplace_error = OSError(
        "marketplace file not found"
    )
    client = _create_client(container)

    response = client.post(
        "/api/system/configs/plugins:install",
        json={
            "source": "quality",
            "marketplace": str(tmp_path / "missing-marketplace.json"),
            "scope": "user",
        },
    )

    assert response.status_code == 400
    assert "marketplace file not found" in response.text
    assert container.reload_count == 0


def test_plugin_validate_and_marketplace_api(tmp_path: Path) -> None:
    container = _FakeContainer(tmp_path / "quality")
    client = _create_client(container)
    marketplace_path = tmp_path / "marketplace.json"
    marketplace_path.write_text('{"plugins": []}', encoding="utf-8")

    validate_response = client.post(
        "/api/system/configs/plugins:validate",
        json={"path": str(tmp_path / "quality")},
    )
    marketplace_response = client.post(
        "/api/system/configs/plugins/marketplace",
        json={"marketplace": str(marketplace_path)},
    )

    assert validate_response.status_code == 200
    assert validate_response.json()["plugins"][0]["name"] == "quality"
    assert validate_response.json()["diagnostics"][0]["message"] == "validated"
    assert marketplace_response.status_code == 200
    assert marketplace_response.json()["plugins"] == []


def test_plugin_marketplace_api_accepts_claude_ref_and_refresh(tmp_path: Path) -> None:
    container = _FakeContainer(tmp_path / "quality")
    client = _create_client(container)

    response = client.post(
        "/api/system/configs/plugins/marketplace",
        json={
            "marketplace": "claude-plugins-official",
            "marketplace_provider": "claude",
            "marketplace_source": str(tmp_path),
            "marketplace_ref": "main",
            "refresh": True,
        },
    )

    assert response.status_code == 400
    assert "Claude marketplace file not found" in response.text


def test_plugin_marketplace_source_preserves_empty_claude_default() -> None:
    source = system._plugin_marketplace_source(
        system.PluginMarketplaceRequest(
            marketplace="claude-plugins-official",
            marketplace_provider=PluginMarketplaceProviderKind.CLAUDE,
        )
    )

    assert source.value == ""


def test_plugin_marketplace_source_resolves_local_claude_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marketplace_root = tmp_path / "claude-marketplace"
    marketplace_root.mkdir()
    monkeypatch.chdir(tmp_path)

    source = system._plugin_marketplace_source(
        system.PluginMarketplaceRequest(
            marketplace="local-claude",
            marketplace_provider=PluginMarketplaceProviderKind.CLAUDE,
            marketplace_source="claude-marketplace",
        )
    )

    assert source.value == str(marketplace_root.resolve())


def test_plugin_enable_disable_update_api_reload_runtime(tmp_path: Path) -> None:
    container = _FakeContainer(tmp_path / "quality")
    client = _create_client(container)

    enable_response = client.post(
        "/api/system/configs/plugins/quality:enable",
        json={"scope": "user"},
    )
    disable_response = client.post(
        "/api/system/configs/plugins/quality:disable",
        json={"scope": "project"},
    )
    update_response = client.post(
        "/api/system/configs/plugins/quality:update",
        json={"scope": "user", "version": "2.0.0"},
    )

    assert enable_response.status_code == 200
    assert disable_response.status_code == 200
    assert update_response.status_code == 200
    assert container.plugin_config_manager.enabled == (
        "quality",
        PluginScope.PROJECT,
        False,
    )
    assert container.plugin_config_manager.updated == (
        "quality",
        PluginScope.USER,
        "2.0.0",
    )
    assert container.reload_count == 3


def test_infer_plugin_install_source_kind() -> None:
    assert system._infer_plugin_install_source_kind("https://example.test/a") == (
        PluginInstallSourceKind.GIT
    )
    assert system._infer_plugin_install_source_kind("plugin.git") == (
        PluginInstallSourceKind.GIT
    )
    assert system._infer_plugin_install_source_kind("C:/plugins/local") == (
        PluginInstallSourceKind.LOCAL
    )


def test_plugin_delete_api_uses_query_scope_and_reloads_runtime(
    tmp_path: Path,
) -> None:
    container = _FakeContainer(tmp_path / "quality")
    client = _create_client(container)

    response = client.delete(
        "/api/system/configs/plugins/quality?scope=project&prune=true",
    )

    assert response.status_code == 200
    assert container.plugin_config_manager.uninstalled == (
        "quality",
        PluginScope.PROJECT,
        True,
    )
    assert container.reload_count == 1


def test_plugin_configure_api_reloads_runtime(tmp_path: Path) -> None:
    container = _FakeContainer(tmp_path / "quality")
    client = _create_client(container)

    response = client.post(
        "/api/system/configs/plugins/quality:configure",
        json={
            "scope": "user",
            "user_config": {"endpoint": "https://docs.test"},
        },
    )

    assert response.status_code == 200
    assert container.plugin_config_manager.configured == (
        "quality",
        PluginScope.USER,
        {"endpoint": "https://docs.test"},
    )
    assert container.reload_count == 1


def _create_client(container: _FakeContainer) -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    app.dependency_overrides[get_container] = lambda: container
    return TestClient(app)


def _plugin_state_record(
    root_dir: Path,
    *,
    scope: PluginScope,
    enabled: bool,
) -> PluginStateRecord:
    return PluginStateRecord(
        name="quality",
        version="1.0.0",
        scope=scope,
        enabled=enabled,
        root_dir=root_dir,
        source=PluginInstallSource(value=str(root_dir)),
    )


def _plugin_record(root_dir: Path) -> PluginRecord:
    return PluginRecord(
        name="quality",
        version="1.0.0",
        scope=PluginScope.USER,
        enabled=True,
        root_dir=root_dir,
        data_dir=root_dir / "data",
        manifest=PluginManifest(name="quality", version="1.0.0"),
    )
