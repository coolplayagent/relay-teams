# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

from agent_teams.builtin import get_builtin_roles_dir
from agent_teams.env.environment_variable_models import (
    EnvironmentVariableSaveRequest,
    EnvironmentVariableScope,
)
from agent_teams.interfaces.server.container import ServerContainer
from agent_teams.roles import RoleLoader


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


def _write_model_config(config_dir: Path, *, api_key: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "model.json").write_text(
        (
            "{\n"
            '  "default": {\n'
            '    "provider": "openai_compatible",\n'
            '    "model": "gpt-4o-mini",\n'
            '    "base_url": "https://example.test/v1",\n'
            f'    "api_key": "{api_key}",\n'
            '    "is_default": true\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )


def _write_app_role(config_dir: Path, *, role_id: str) -> None:
    roles_dir = config_dir / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    (roles_dir / f"{role_id}.md").write_text(
        (
            "---\n"
            f"role_id: {role_id}\n"
            "name: Planner\n"
            "description: Runtime-added planning role.\n"
            "version: 1.0.0\n"
            "tools:\n"
            "  - grep\n"
            "---\n\n"
            "Plan carefully.\n"
        ),
        encoding="utf-8",
    )


def test_runtime_reload_updates_run_manager_provider_factory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    container = ServerContainer(config_dir=config_dir)

    previous_provider_factory = container.run_service._provider_factory

    container.model_config_service.reload_model_config()

    assert container.run_service._provider_factory is container._provider_factory
    assert container.run_service._provider_factory is not previous_provider_factory


def test_roles_reload_updates_long_lived_role_registry_references(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    container = ServerContainer(config_dir=config_dir)
    _write_app_role(config_dir, role_id="planner")
    registry = RoleLoader().load_builtin_and_app(
        builtin_roles_dir=get_builtin_roles_dir(),
        app_roles_dir=container.runtime.paths.roles_dir,
    )

    container._on_roles_reloaded(registry)

    assert container.runtime_role_resolver._role_registry is registry
    assert container.session_service._role_registry is registry
    assert container.run_service._role_registry is registry
    assert container.feishu_gateway_service._role_registry is registry
    assert container.wechat_gateway_service._role_registry is registry
    assert (
        container.runtime_role_resolver.get_effective_role(
            run_id=None,
            role_id="planner",
        ).role_id
        == "planner"
    )
    assert container.feishu_gateway_service._resolve_normal_root_role_id("planner") == (
        "planner"
    )


def test_saving_environment_variable_reloads_model_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env_key = "AGENT_TEAMS_RUNTIME_RELOAD_TEST_API_KEY"
    _clear_proxy_env(monkeypatch)
    monkeypatch.delenv(env_key, raising=False)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key=f"${{{env_key}}}")
    container = ServerContainer(config_dir=config_dir)

    assert container.runtime.model_status.loaded is False

    container.environment_variable_service.save_environment_variable(
        scope=EnvironmentVariableScope.APP,
        key=env_key,
        request=EnvironmentVariableSaveRequest(value="secret-key"),
    )

    assert container.runtime.model_status.loaded is True
    assert container.runtime.llm_profiles["default"].api_key == "secret-key"


def test_proxy_environment_variable_change_triggers_proxy_runtime_refresh(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    container = ServerContainer(config_dir=config_dir)
    feishu_reload_calls: list[str] = []
    wechat_reload_calls: list[str] = []
    mcp_reload_calls: list[str] = []

    monkeypatch.setattr(
        container.feishu_subscription_service,
        "reload",
        lambda: feishu_reload_calls.append("feishu"),
    )
    monkeypatch.setattr(
        container.wechat_gateway_service,
        "reload",
        lambda: wechat_reload_calls.append("wechat"),
    )
    monkeypatch.setattr(
        container.mcp_config_manager,
        "load_registry",
        lambda: (mcp_reload_calls.append("mcp"), container.mcp_registry)[1],
    )

    container.environment_variable_service.save_environment_variable(
        scope=EnvironmentVariableScope.APP,
        key="HTTP_PROXY",
        request=EnvironmentVariableSaveRequest(value="http://proxy.example:8080"),
    )

    assert os.environ["HTTP_PROXY"] == "http://proxy.example:8080"
    assert container.proxy_config_service.get_proxy_config().http_proxy == (
        "http://proxy.example:8080"
    )
    assert feishu_reload_calls == ["feishu"]
    assert wechat_reload_calls == ["wechat"]
    assert mcp_reload_calls == ["mcp"]
