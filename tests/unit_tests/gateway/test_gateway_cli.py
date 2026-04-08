# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

import relay_teams.gateway.gateway_cli as gateway_cli


class _FakeMcpService:
    def replace_registry(self, registry: object) -> None:
        self.registry = registry


class _FakeRuntimePaths:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path


class _FakeRuntime:
    def __init__(self, db_path: Path) -> None:
        self.paths = _FakeRuntimePaths(db_path)


class _FakeContainer:
    def __init__(
        self, *, config_dir: Path, session_model_profile_lookup: object
    ) -> None:
        _ = session_model_profile_lookup
        self.config_dir = config_dir
        self.mcp_registry = object()
        self.mcp_service = _FakeMcpService()
        self.runtime = _FakeRuntime(config_dir / "gateway.db")
        self.session_service = object()
        self.workspace_service = object()
        self.run_service = object()
        self.session_ingress_service = object()
        self.media_asset_service = object()
        self.role_registry = _FakeRoleRegistry()
        self.refreshed = False

    def _refresh_coordinator_runtime(self) -> None:
        self.refreshed = True


class _FakeGatewaySessionService:
    captured_kwargs: dict[str, object] | None = None

    def __init__(self, **kwargs: object) -> None:
        type(self).captured_kwargs = dict(kwargs)


class _FakeRoleRegistry:
    def resolve_normal_mode_role_id(self, role_id: str | None) -> str:
        normalized = str(role_id or "").strip()
        if not normalized:
            return "MainAgent"
        if normalized == "Coordinator":
            raise ValueError(
                "Coordinator role cannot be used in normal mode: Coordinator"
            )
        if normalized == "Crafter":
            return normalized
        raise ValueError(f"Unknown normal mode role: {normalized}")

    def list_normal_mode_roles(self) -> tuple[object, ...]:
        return (
            type("RoleEntry", (), {"role_id": "MainAgent"})(),
            type("RoleEntry", (), {"role_id": "Crafter"})(),
        )


class _FakeAcpGatewayServer:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.notify = None

    def set_notify(self, notify: object) -> None:
        self.notify = notify


class _FakeAcpStdioRuntime:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.send_message = object()


def test_build_acp_stdio_runtime_passes_workspace_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(gateway_cli, "get_app_config_dir", lambda: tmp_path)
    monkeypatch.setattr(gateway_cli, "ensure_app_config_bootstrap", lambda _path: None)
    monkeypatch.setattr(gateway_cli, "sync_app_env_to_process_env", lambda _path: None)
    monkeypatch.setattr(
        gateway_cli,
        "configure_logging",
        lambda *, config_dir, console_enabled_override: None,
    )
    monkeypatch.setattr(gateway_cli, "ServerContainer", _FakeContainer)
    monkeypatch.setattr(
        gateway_cli,
        "GatewaySessionRepository",
        lambda _db_path: object(),
    )
    monkeypatch.setattr(
        gateway_cli,
        "GatewaySessionService",
        _FakeGatewaySessionService,
    )
    monkeypatch.setattr(gateway_cli, "AcpGatewayServer", _FakeAcpGatewayServer)
    monkeypatch.setattr(gateway_cli, "AcpStdioRuntime", _FakeAcpStdioRuntime)

    _ = gateway_cli._build_acp_stdio_runtime()

    captured = _FakeGatewaySessionService.captured_kwargs
    assert captured is not None
    assert "workspace_service" in captured
    assert captured["workspace_service"] is not None


def test_build_acp_stdio_runtime_passes_default_normal_root_role(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(gateway_cli, "get_app_config_dir", lambda: tmp_path)
    monkeypatch.setattr(gateway_cli, "ensure_app_config_bootstrap", lambda _path: None)
    monkeypatch.setattr(gateway_cli, "sync_app_env_to_process_env", lambda _path: None)
    monkeypatch.setattr(
        gateway_cli,
        "configure_logging",
        lambda *, config_dir, console_enabled_override: None,
    )
    monkeypatch.setattr(gateway_cli, "ServerContainer", _FakeContainer)
    monkeypatch.setattr(
        gateway_cli,
        "GatewaySessionRepository",
        lambda _db_path: object(),
    )
    monkeypatch.setattr(
        gateway_cli,
        "GatewaySessionService",
        _FakeGatewaySessionService,
    )
    monkeypatch.setattr(gateway_cli, "AcpGatewayServer", _FakeAcpGatewayServer)
    monkeypatch.setattr(gateway_cli, "AcpStdioRuntime", _FakeAcpStdioRuntime)

    _ = gateway_cli._build_acp_stdio_runtime(role_id="Crafter")

    captured = _FakeGatewaySessionService.captured_kwargs
    assert captured is not None
    assert captured["default_normal_root_role_id"] == "Crafter"


def test_build_acp_stdio_runtime_rejects_invalid_default_normal_root_role(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(gateway_cli, "get_app_config_dir", lambda: tmp_path)
    monkeypatch.setattr(gateway_cli, "ensure_app_config_bootstrap", lambda _path: None)
    monkeypatch.setattr(gateway_cli, "sync_app_env_to_process_env", lambda _path: None)
    monkeypatch.setattr(
        gateway_cli,
        "configure_logging",
        lambda *, config_dir, console_enabled_override: None,
    )
    monkeypatch.setattr(gateway_cli, "ServerContainer", _FakeContainer)
    monkeypatch.setattr(
        gateway_cli,
        "GatewaySessionRepository",
        lambda _db_path: object(),
    )
    monkeypatch.setattr(
        gateway_cli,
        "GatewaySessionService",
        _FakeGatewaySessionService,
    )
    monkeypatch.setattr(gateway_cli, "AcpGatewayServer", _FakeAcpGatewayServer)
    monkeypatch.setattr(gateway_cli, "AcpStdioRuntime", _FakeAcpStdioRuntime)

    with pytest.raises(Exception, match="Invalid --role 'Missing'.*MainAgent, Crafter"):
        _ = gateway_cli._build_acp_stdio_runtime(role_id="Missing")
