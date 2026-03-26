# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from pydantic_ai.messages import ModelRequest, UserPromptPart

import agent_teams.external_agents.provider as provider_module
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.external_agents.host_tool_bridge import HOST_TOOL_SERVER_ID
from agent_teams.external_agents.models import ExternalAgentConfig, StdioTransportConfig
from agent_teams.external_agents.config_service import ExternalAgentConfigService
from agent_teams.external_agents.provider import ExternalAcpSessionManager
from agent_teams.external_agents.session_repository import (
    ExternalAgentSessionRepository,
)
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.notifications import NotificationService
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.providers.provider_contracts import LLMRequest
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.tools.feishu_tools import FeishuToolService
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.workspace import WorkspaceManager


class _FakeConfigService:
    def __init__(self, agent: ExternalAgentConfig) -> None:
        self._agent = agent

    def resolve_runtime_agent(self, agent_id: str) -> ExternalAgentConfig:
        assert agent_id == self._agent.agent_id
        return self._agent


class _FakeSessionRepo:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], object] = {}

    def get(self, *, session_id: str, role_id: str, agent_id: str) -> object | None:
        return self._records.get((session_id, role_id, agent_id))

    def upsert(self, record: object) -> object:
        session_id = str(getattr(record, "session_id"))
        role_id = str(getattr(record, "role_id"))
        agent_id = str(getattr(record, "agent_id"))
        self._records[(session_id, role_id, agent_id)] = record
        return record

    def delete(self, *, session_id: str, role_id: str, agent_id: str) -> None:
        self._records.pop((session_id, role_id, agent_id), None)


class _FakeMessageRepo:
    def __init__(self, prompt_text: str) -> None:
        self._history = [ModelRequest(parts=[UserPromptPart(content=prompt_text)])]
        self.append_calls: list[dict[str, object]] = []

    def get_history_for_conversation_task(
        self,
        _conversation_id: str,
        _task_id: str,
    ) -> list[ModelRequest]:
        return list(self._history)

    def append(self, **kwargs: object) -> None:
        self.append_calls.append(kwargs)


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)


class _FakeWorkspaceHandle:
    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir

    def resolve_workdir(self) -> Path:
        return self._workdir


class _FakeWorkspaceManager:
    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir

    def resolve(self, **_kwargs: object) -> _FakeWorkspaceHandle:
        return _FakeWorkspaceHandle(self._workdir)


class _FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, JsonValue]]] = []
        self.notifications: list[tuple[str, dict[str, JsonValue]]] = []

    async def start(self) -> None:
        return None

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        self.requests.append((method, params))
        if method == "initialize":
            return {"protocolVersion": 1}
        if method in {"session/new", "session/load"}:
            return {"sessionId": "remote-1"}
        if method == "session/prompt":
            return {"stopReason": "end_turn"}
        raise AssertionError(f"Unexpected request: {method}")

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        self.notifications.append((method, params))

    async def close(self) -> None:
        return None


class _FakeHostToolBridge:
    def __init__(self, *, has_tools: bool) -> None:
        self.has_tools_value = has_tools
        self.active_request: LLMRequest | None = None
        self.configure_calls: list[dict[str, object]] = []
        self.stdio_payload_calls: list[dict[str, object]] = []
        self.open_calls: list[str] = []
        self.relay_calls: list[dict[str, object]] = []
        self.close_calls: list[str] = []

    async def configure(self, **kwargs: object) -> bool:
        self.configure_calls.append(kwargs)
        return False

    def has_tools(self) -> bool:
        return self.has_tools_value

    def stdio_server_payload(
        self,
        *,
        config_dir: Path,
        request: LLMRequest,
    ) -> dict[str, JsonValue] | None:
        if not self.has_tools_value:
            return None
        self.stdio_payload_calls.append(
            {
                "config_dir": config_dir,
                "request": request,
            }
        )
        return {
            "name": HOST_TOOL_SERVER_ID,
            "command": "python",
            "args": ["-m", "agent_teams.external_agents.host_tool_stdio_server"],
            "env": [
                {"name": "AGENT_TEAMS_CONFIG_DIR", "value": str(config_dir)},
                {"name": "AGENT_TEAMS_HOST_TOOL_RUN_ID", "value": request.run_id},
                {"name": "AGENT_TEAMS_HOST_TOOL_TASK_ID", "value": request.task_id},
            ],
        }

    def bind_active_request(self, request: LLMRequest) -> None:
        self.active_request = request

    def clear_active_request(self) -> None:
        self.active_request = None

    async def open_connection(self, *, server_id: str) -> dict[str, JsonValue]:
        self.open_calls.append(server_id)
        return {
            "connectionId": "conn-1",
            "serverId": server_id,
            "status": "open",
        }

    async def relay_message(
        self,
        *,
        connection_id: str,
        method: str,
        params: dict[str, JsonValue],
        message_id: str | int | None,
    ) -> dict[str, JsonValue]:
        self.relay_calls.append(
            {
                "connection_id": connection_id,
                "method": method,
                "params": params,
                "message_id": message_id,
            }
        )
        return {"result": {"ok": True}}

    async def close_connection(self, *, connection_id: str) -> dict[str, JsonValue]:
        self.close_calls.append(connection_id)
        return {"status": "closed", "connectionId": connection_id}

    async def close(self) -> None:
        return None


def _build_role() -> RoleDefinition:
    return RoleDefinition(
        role_id="spec_coder",
        name="Spec Coder",
        description="Implements requested changes.",
        version="1.0.0",
        tools=("shell",),
        mcp_servers=(),
        skills=(),
        model_profile="default",
        bound_agent_id="agent-1",
        system_prompt="Follow the role prompt exactly.",
    )


def _build_request() -> LLMRequest:
    return LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id="spec_coder",
        system_prompt="Provider system prompt text.",
        user_prompt="Fallback user prompt.",
    )


def _build_agent() -> ExternalAgentConfig:
    return ExternalAgentConfig(
        agent_id="agent-1",
        name="ACP Agent",
        description="External ACP agent.",
        transport=StdioTransportConfig(command="acp-agent"),
    )


def _build_manager(
    *,
    monkeypatch: pytest.MonkeyPatch,
    transport: _FakeTransport,
    bridge: _FakeHostToolBridge,
    prompt_text: str,
    workdir: Path,
    config_dir: Path,
) -> tuple[ExternalAcpSessionManager, dict[str, object]]:
    captured: dict[str, object] = {}
    agent = _build_agent()

    def fake_build_acp_transport(
        *,
        config: ExternalAgentConfig,
        on_message,
        runtime_cwd: str | None = None,
    ) -> _FakeTransport:
        captured["config"] = config
        captured["on_message"] = on_message
        captured["runtime_cwd"] = runtime_cwd
        return transport

    monkeypatch.setattr(
        provider_module, "build_acp_transport", fake_build_acp_transport
    )

    manager = ExternalAcpSessionManager(
        config_dir=config_dir,
        config_service=cast(ExternalAgentConfigService, _FakeConfigService(agent)),
        session_repo=cast(ExternalAgentSessionRepository, _FakeSessionRepo()),
        message_repo=cast(MessageRepository, _FakeMessageRepo(prompt_text)),
        run_event_hub=cast(RunEventHub, _FakeRunEventHub()),
        workspace_manager=cast(WorkspaceManager, _FakeWorkspaceManager(workdir)),
        task_repo=cast(TaskRepository, object()),
        shared_store=cast(SharedStateRepository, object()),
        event_bus=cast(EventLog, object()),
        injection_manager=cast(RunInjectionManager, object()),
        agent_repo=cast(AgentInstanceRepository, object()),
        approval_ticket_repo=cast(ApprovalTicketRepository, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        run_intent_repo=cast(RunIntentRepository, object()),
        role_memory_service=cast(RoleMemoryService | None, None),
        tool_registry=cast(ToolRegistry, object()),
        get_mcp_registry=lambda: cast(McpRegistry, object()),
        get_skill_registry=lambda: cast(SkillRegistry, object()),
        get_role_registry=lambda: cast(RoleRegistry, object()),
        get_task_execution_service=lambda: cast(TaskExecutionService, object()),
        get_task_service=lambda: cast(TaskOrchestrationService, object()),
        run_control_manager=cast(RunControlManager, object()),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
        get_notification_service=lambda: cast(NotificationService | None, None),
        feishu_tool_service=cast(FeishuToolService | None, None),
    )
    monkeypatch.setattr(manager, "_create_host_tool_bridge", lambda: bridge)
    return manager, captured


@pytest.mark.asyncio
async def test_external_acp_prompt_includes_system_prompt_and_host_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _FakeTransport()
    bridge = _FakeHostToolBridge(has_tools=True)
    manager, captured = _build_manager(
        monkeypatch=monkeypatch,
        transport=transport,
        bridge=bridge,
        prompt_text="Summarize the architecture.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )

    output = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    assert output == ""
    assert captured["runtime_cwd"] == str(tmp_path)
    assert [method for method, _ in transport.requests] == [
        "initialize",
        "session/new",
        "session/prompt",
    ]
    session_new_payload = transport.requests[1][1]
    assert session_new_payload["mcpServers"] == [
        bridge.stdio_server_payload(
            config_dir=tmp_path / "config",
            request=_build_request(),
        )
    ]
    prompt_payload = transport.requests[2][1]
    prompt_parts = cast(list[dict[str, object]], prompt_payload["prompt"])
    prompt_text = str(prompt_parts[0]["text"])
    assert "## Role Prompt" in prompt_text
    assert "Provider system prompt text." in prompt_text
    assert "## Host Tools" in prompt_text
    assert "agent_teams_*" in prompt_text
    assert "## User Prompt" in prompt_text
    assert "Summarize the architecture." in prompt_text
    assert bridge.active_request is None


@pytest.mark.asyncio
async def test_external_acp_refreshes_remote_session_when_prompt_scoped_mcp_signature_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _FakeTransport()
    bridge = _FakeHostToolBridge(has_tools=True)
    manager, _ = _build_manager(
        monkeypatch=monkeypatch,
        transport=transport,
        bridge=bridge,
        prompt_text="Summarize the architecture.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )
    role = _build_role()
    request = _build_request()
    request_two = request.model_copy(update={"run_id": "run-2", "task_id": "task-2"})

    _ = await manager.prompt(agent_id="agent-1", role=role, request=request)
    _ = await manager.prompt(agent_id="agent-1", role=role, request=request_two)

    assert [method for method, _ in transport.requests] == [
        "initialize",
        "session/new",
        "session/prompt",
        "session/load",
        "session/prompt",
    ]
    session_load_payload = transport.requests[3][1]
    mcp_servers = cast(list[dict[str, JsonValue]], session_load_payload["mcpServers"])
    assert len(mcp_servers) == 1
    env = cast(list[dict[str, str]], mcp_servers[0]["env"])
    assert {"name": "AGENT_TEAMS_HOST_TOOL_RUN_ID", "value": "run-2"} in env
    assert {"name": "AGENT_TEAMS_HOST_TOOL_TASK_ID", "value": "task-2"} in env


@pytest.mark.asyncio
async def test_external_acp_routes_mcp_callbacks_to_host_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _FakeTransport()
    bridge = _FakeHostToolBridge(has_tools=True)
    manager, captured = _build_manager(
        monkeypatch=monkeypatch,
        transport=transport,
        bridge=bridge,
        prompt_text="Summarize the architecture.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )
    on_message = cast(
        Callable[
            [str, dict[str, JsonValue], str | int | None],
            Awaitable[dict[str, JsonValue]],
        ],
        captured["on_message"],
    )

    connect_result = await on_message(
        "mcp/connect",
        {"sessionId": "remote-1", "serverId": HOST_TOOL_SERVER_ID},
        10,
    )
    message_result = await on_message(
        "mcp/message",
        {
            "sessionId": "remote-1",
            "connectionId": "conn-1",
            "method": "tools/list",
            "params": {},
        },
        11,
    )
    disconnect_result = await on_message(
        "mcp/disconnect",
        {
            "sessionId": "remote-1",
            "connectionId": "conn-1",
        },
        12,
    )

    assert connect_result == {
        "connectionId": "conn-1",
        "serverId": HOST_TOOL_SERVER_ID,
        "status": "open",
    }
    assert message_result == {"result": {"ok": True}}
    assert disconnect_result == {"status": "closed", "connectionId": "conn-1"}
    assert bridge.open_calls == [HOST_TOOL_SERVER_ID]
    assert bridge.relay_calls == [
        {
            "connection_id": "conn-1",
            "method": "tools/list",
            "params": {},
            "message_id": 11,
        }
    ]
    assert bridge.close_calls == ["conn-1"]
