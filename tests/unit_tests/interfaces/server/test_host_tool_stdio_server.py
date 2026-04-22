# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import relay_teams.interfaces.server.host_tool_stdio_server as stdio_server


class _FakeStdioServer:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, object]] = []

    async def run_stdio_async(
        self,
        *,
        show_banner: bool,
        log_level: str,
    ) -> None:
        self.run_calls.append(
            {
                "show_banner": show_banner,
                "log_level": log_level,
            }
        )


class _FakeBridge:
    last_instance: _FakeBridge | None = None

    def __init__(self, **kwargs: object) -> None:
        self.init_kwargs = kwargs
        self.configure_calls: list[dict[str, object]] = []
        self.bound_requests: list[object] = []
        self.close_calls = 0
        self._server = _FakeStdioServer()
        type(self).last_instance = self

    async def configure(self, **kwargs: object) -> None:
        self.configure_calls.append(kwargs)

    def bind_active_request(self, request: object) -> None:
        self.bound_requests.append(request)

    def require_server(self) -> _FakeStdioServer:
        return self._server

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_run_stdio_server_passes_todo_service_to_host_tool_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    fake_role = object()
    fake_todo_service = object()
    fake_resolve_model_config = object()
    fake_container = SimpleNamespace(
        role_registry=SimpleNamespace(get=lambda role_id: fake_role),
        task_repo=object(),
        shared_store=object(),
        event_log=object(),
        injection_manager=object(),
        run_event_hub=object(),
        agent_repo=object(),
        approval_ticket_repo=object(),
        user_question_repo=object(),
        run_runtime_repo=object(),
        run_intent_repo=object(),
        background_task_service=object(),
        todo_service=fake_todo_service,
        monitor_service=object(),
        workspace_manager=object(),
        media_asset_service=object(),
        role_memory_service=object(),
        tool_registry=object(),
        message_repo=object(),
        mcp_registry=object(),
        skill_registry=object(),
        task_execution_service=object(),
        task_service=object(),
        run_control_manager=object(),
        tool_approval_manager=object(),
        user_question_manager=object(),
        tool_approval_policy=object(),
        shell_approval_repo=object(),
        notification_service=object(),
        metric_recorder=object(),
        im_tool_service=object(),
        computer_runtime=object(),
        resolve_external_agent_model_config=fake_resolve_model_config,
    )

    monkeypatch.setenv(stdio_server.HOST_TOOL_CONFIG_DIR_ENV, str(config_dir))
    monkeypatch.setenv(stdio_server.HOST_TOOL_ROLE_ID_ENV, "MainAgent")
    monkeypatch.setenv(stdio_server.HOST_TOOL_RUN_ID_ENV, "run-1")
    monkeypatch.setenv(stdio_server.HOST_TOOL_TRACE_ID_ENV, "trace-1")
    monkeypatch.setenv(stdio_server.HOST_TOOL_TASK_ID_ENV, "task-1")
    monkeypatch.setenv(stdio_server.HOST_TOOL_SESSION_ID_ENV, "session-1")
    monkeypatch.setenv(stdio_server.HOST_TOOL_WORKSPACE_ID_ENV, "workspace-1")
    monkeypatch.setenv(stdio_server.HOST_TOOL_INSTANCE_ID_ENV, "instance-1")
    monkeypatch.setattr(
        stdio_server,
        "ServerContainer",
        lambda **kwargs: fake_container,
    )
    monkeypatch.setattr(
        stdio_server,
        "ExternalAcpHostToolBridge",
        _FakeBridge,
    )

    await stdio_server._run_stdio_server()

    bridge = _FakeBridge.last_instance
    assert bridge is not None
    assert bridge.init_kwargs["todo_service"] is fake_todo_service
    assert bridge.init_kwargs["resolve_model_config"] is fake_resolve_model_config
    assert bridge.configure_calls[0]["role"] is fake_role
    assert bridge.configure_calls[0]["session_id"] == "session-1"
    assert len(bridge.bound_requests) == 1
    assert bridge._server.run_calls == [
        {
            "show_banner": False,
            "log_level": "ERROR",
        }
    ]
    assert bridge.close_calls == 1
