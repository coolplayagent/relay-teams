# -*- coding: utf-8 -*-
from __future__ import annotations

import os

import pytest
from pathlib import Path

from relay_teams.builtin import get_builtin_roles_dir
from relay_teams.env.environment_variable_models import (
    EnvironmentVariableSaveRequest,
    EnvironmentVariableScope,
)
from relay_teams.interfaces.server.container import ServerContainer
from relay_teams.providers.model_fallback import LlmFallbackMiddleware
from relay_teams.roles import RoleLoader
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)


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


def test_container_injects_fallback_middleware_into_reflection_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    container = ServerContainer(config_dir=config_dir)

    reflection_service = container._build_subagent_reflection_service()

    assert reflection_service is not None
    assert isinstance(reflection_service._fallback_middleware, LlmFallbackMiddleware)


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

    reloaded_registry = container.role_registry

    assert reloaded_registry is not registry
    assert container.runtime_role_resolver._role_registry is reloaded_registry
    assert container.session_service._role_registry is reloaded_registry
    assert container.run_service._role_registry is reloaded_registry
    assert container.feishu_gateway_service._role_registry is reloaded_registry
    assert container.wechat_gateway_service._role_registry is reloaded_registry
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


def test_container_tolerates_missing_builtin_roles_on_startup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    missing_builtin_roles_dir = tmp_path / "missing_builtin_roles"
    missing_builtin_roles_dir.mkdir()
    monkeypatch.setattr(
        "relay_teams.interfaces.server.container.get_builtin_roles_dir",
        lambda: missing_builtin_roles_dir,
    )

    container = ServerContainer(config_dir=config_dir)

    assert container.role_registry.list_roles() == ()


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


def test_saving_app_environment_variable_reloads_mcp_and_skills_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env_key = "AGENT_TEAMS_RUNTIME_RELOAD_TEST_ENV"
    _clear_proxy_env(monkeypatch)
    monkeypatch.delenv(env_key, raising=False)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    container = ServerContainer(config_dir=config_dir)
    mcp_reload_calls: list[str] = []
    skill_reload_calls: list[str] = []

    monkeypatch.setattr(
        container.mcp_config_manager,
        "load_registry",
        lambda: (mcp_reload_calls.append("mcp"), container.mcp_registry)[1],
    )
    monkeypatch.setattr(
        container.skills_config_reload_service,
        "reload_skills_config",
        lambda: skill_reload_calls.append("skills"),
    )

    container.environment_variable_service.save_environment_variable(
        scope=EnvironmentVariableScope.APP,
        key=env_key,
        request=EnvironmentVariableSaveRequest(value="enabled"),
    )

    assert mcp_reload_calls == ["mcp"]
    assert skill_reload_calls == ["skills"]


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
    skill_reload_calls: list[str] = []

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
    monkeypatch.setattr(
        container.skills_config_reload_service,
        "reload_skills_config",
        lambda: skill_reload_calls.append("skills"),
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
    assert skill_reload_calls == ["skills"]


@pytest.mark.asyncio
async def test_container_binds_background_completion_sink_during_start(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    container = ServerContainer(config_dir=config_dir)

    start_calls: list[str] = []
    lifecycle_calls: list[str] = []
    original_bind_event_loop = container.run_service.bind_event_loop
    original_bind_completion_sink = (
        container.background_task_service.bind_completion_sink
    )

    def _record_bind_event_loop(loop) -> None:
        lifecycle_calls.append("bind_event_loop")
        original_bind_event_loop(loop)

    def _record_bind_completion_sink(sink) -> None:
        lifecycle_calls.append("bind_completion_sink")
        original_bind_completion_sink(sink)

    monkeypatch.setattr(
        container.run_service, "bind_event_loop", _record_bind_event_loop
    )
    monkeypatch.setattr(
        container.background_task_service,
        "bind_completion_sink",
        _record_bind_completion_sink,
    )

    monkeypatch.setattr(
        container.wechat_gateway_service,
        "start",
        lambda: start_calls.append("wechat"),
    )
    monkeypatch.setattr(
        container.feishu_subscription_service,
        "start",
        lambda: start_calls.append("feishu-subscription"),
    )
    monkeypatch.setattr(
        container.feishu_message_pool_service,
        "start",
        lambda: start_calls.append("feishu-message-pool"),
    )
    monkeypatch.setattr(
        container.automation_delivery_worker,
        "start",
        lambda: start_calls.append("automation-delivery"),
    )
    monkeypatch.setattr(
        container.automation_bound_session_queue_worker,
        "start",
        lambda: start_calls.append("automation-bound-session"),
    )

    async def _fake_scheduler_start() -> None:
        start_calls.append("scheduler")

    monkeypatch.setattr(
        container.automation_scheduler_service,
        "start",
        _fake_scheduler_start,
    )

    assert container.background_task_service._completion_sink is None

    await container.start()

    assert lifecycle_calls == ["bind_event_loop", "bind_completion_sink"]
    assert container.background_task_service._completion_sink is container.run_service
    assert start_calls == [
        "wechat",
        "feishu-subscription",
        "feishu-message-pool",
        "automation-delivery",
        "automation-bound-session",
        "scheduler",
    ]


def test_container_wires_automation_bound_session_queue_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    container = ServerContainer(config_dir=config_dir)

    assert container.automation_service._bound_session_queue_service is (
        container.automation_bound_session_queue_service
    )
    assert (
        container.automation_bound_session_queue_worker._queue_service
        is container.automation_bound_session_queue_service
    )


def test_container_interrupts_persisted_background_processes_before_marking_stopped(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from relay_teams.interfaces.server import container as container_module

    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    lifecycle: list[str] = []
    interruptible = (
        BackgroundTaskRecord(
            background_task_id="exec-running",
            run_id="run-1",
            session_id="session-1",
            command="sleep 30",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.RUNNING,
            pid=3210,
            log_path="tmp/background_tasks/exec-running.log",
        ),
        BackgroundTaskRecord(
            background_task_id="exec-missing-pid",
            run_id="run-1",
            session_id="session-1",
            command="sleep 60",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.BLOCKED,
            log_path="tmp/background_tasks/exec-missing-pid.log",
        ),
    )

    monkeypatch.setattr(
        container_module.BackgroundTaskRepository,
        "list_interruptible",
        lambda self: interruptible,
    )
    monkeypatch.setattr(
        container_module,
        "kill_process_tree_by_pid",
        lambda pid: lifecycle.append(f"kill:{pid}") or True,
    )
    monkeypatch.setattr(
        container_module.BackgroundTaskRepository,
        "mark_transient_background_tasks_interrupted",
        lambda self, *, background_task_ids=None: (
            lifecycle.append(f"mark:{background_task_ids}")
            or len(background_task_ids or ())
        ),
    )

    _ = ServerContainer(config_dir=config_dir)

    assert lifecycle == [
        "kill:3210",
        "mark:('exec-running', 'exec-missing-pid')",
    ]


def test_container_preserves_background_task_rows_when_startup_kill_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from relay_teams.interfaces.server import container as container_module

    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    _write_model_config(config_dir, api_key="initial-secret")
    lifecycle: list[str] = []
    interruptible = (
        BackgroundTaskRecord(
            background_task_id="exec-failed-kill",
            run_id="run-1",
            session_id="session-1",
            command="sleep 30",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.RUNNING,
            pid=3210,
            log_path="tmp/background_tasks/exec-failed-kill.log",
        ),
        BackgroundTaskRecord(
            background_task_id="exec-killed",
            run_id="run-1",
            session_id="session-1",
            command="sleep 60",
            cwd=str(tmp_path),
            status=BackgroundTaskStatus.BLOCKED,
            pid=6543,
            log_path="tmp/background_tasks/exec-killed.log",
        ),
    )

    monkeypatch.setattr(
        container_module.BackgroundTaskRepository,
        "list_interruptible",
        lambda self: interruptible,
    )
    monkeypatch.setattr(
        container_module,
        "kill_process_tree_by_pid",
        lambda pid: lifecycle.append(f"kill:{pid}") or pid == 6543,
    )
    monkeypatch.setattr(
        container_module.BackgroundTaskRepository,
        "mark_transient_background_tasks_interrupted",
        lambda self, *, background_task_ids=None: (
            lifecycle.append(f"mark:{background_task_ids}")
            or len(background_task_ids or ())
        ),
    )

    _ = ServerContainer(config_dir=config_dir)

    assert lifecycle == [
        "kill:3210",
        "kill:6543",
        "mark:('exec-killed',)",
    ]
