# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
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
from agent_teams.external_agents.config_service import ExternalAgentConfigService
from agent_teams.external_agents.host_tool_bridge import (
    HOST_TOOL_SERVER_ID,
    ExternalAcpHostToolBridge,
)
from agent_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentSessionRecord,
    StdioTransportConfig,
)
from agent_teams.external_agents.provider import (
    _ActivePromptState,
    _annotate_external_computer_tool_result,
    _ConversationHandle,
    _conversation_key,
    _extract_tool_result,
    ExternalAcpSessionManager,
)
from agent_teams.external_agents.session_repository import (
    ExternalAgentSessionRepository,
)
from agent_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.notifications import NotificationService
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.providers.model_config import (
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
    SamplingConfig,
)
from agent_teams.providers.provider_contracts import LLMRequest
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.gateway.im import ImToolService
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.workspace import WorkspaceManager

_TransportMessageHandler = Callable[
    [str, dict[str, JsonValue], str | int | None],
    Awaitable[dict[str, JsonValue]],
]


class _FakeConfigService:
    def __init__(self, agent: ExternalAgentConfig) -> None:
        self._agent = agent

    def resolve_runtime_agent(self, agent_id: str) -> ExternalAgentConfig:
        assert agent_id == self._agent.agent_id
        return self._agent


class _FakeSessionRepo:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], ExternalAgentSessionRecord] = {}

    def get(
        self,
        *,
        session_id: str,
        role_id: str,
        agent_id: str,
    ) -> ExternalAgentSessionRecord | None:
        return self._records.get((session_id, role_id, agent_id))

    def upsert(
        self,
        record: ExternalAgentSessionRecord,
    ) -> ExternalAgentSessionRecord:
        self._records[(record.session_id, record.role_id, record.agent_id)] = record
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


class _RequestCapturingTransport:
    def __init__(self, *, response_text: str = "External agent output.") -> None:
        self.response_text = response_text
        self.requests: list[tuple[str, dict[str, JsonValue]]] = []
        self.notifications: list[tuple[str, dict[str, JsonValue]]] = []
        self.on_message: _TransportMessageHandler | None = None
        self.close_calls = 0

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
            if self.on_message is not None:
                await self.on_message(
                    "session/update",
                    {
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": self.response_text,
                            },
                        }
                    },
                    None,
                )
            return {"stopReason": "end_turn"}
        raise AssertionError(f"Unexpected request: {method}")

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        self.notifications.append((method, params))

    async def close(self) -> None:
        self.close_calls += 1
        return None


class _HangingPromptTransport:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, JsonValue]]] = []
        self.prompt_started = asyncio.Event()

    async def start(self) -> None:
        return None

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if method != "session/prompt":
            return {}
        _ = params
        self.prompt_started.set()
        future: asyncio.Future[dict[str, JsonValue]] = asyncio.Future()
        return await future

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        self.notifications.append((method, params))

    async def close(self) -> None:
        return None


class _SequencedPromptTransport:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, JsonValue]]] = []
        self.prompt_started: asyncio.Queue[int] = asyncio.Queue()
        self.gates: list[asyncio.Event] = []
        self.requests: list[dict[str, JsonValue]] = []

    async def start(self) -> None:
        return None

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if method != "session/prompt":
            return {}
        self.requests.append(params)
        gate = asyncio.Event()
        self.gates.append(gate)
        await self.prompt_started.put(len(self.gates))
        await gate.wait()
        return {}

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        self.notifications.append((method, params))

    async def close(self) -> None:
        return None


class _DeferredPromptTransport:
    def __init__(self, *, response_text: str) -> None:
        self.response_text = response_text
        self.on_message: _TransportMessageHandler | None = None

    async def start(self) -> None:
        return None

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if method == "initialize":
            return {"protocolVersion": 1}
        if method in {"session/new", "session/load"}:
            return {"sessionId": "remote-1"}
        if method != "session/prompt":
            raise AssertionError(f"Unexpected request: {method}")
        _ = params
        if self.on_message is not None:
            handler = self.on_message
            loop = asyncio.get_running_loop()
            update_params = cast(
                dict[str, JsonValue],
                {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": self.response_text,
                        },
                    }
                },
            )

            async def _publish_update() -> None:
                await handler("session/update", update_params, None)

            loop.call_soon(asyncio.create_task, _publish_update())
        return {"stopReason": "end_turn"}

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        _ = method
        _ = params

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


def _build_agent(
    *,
    command: str = "acp-agent",
    args: tuple[str, ...] = (),
) -> ExternalAgentConfig:
    return ExternalAgentConfig(
        agent_id="agent-1",
        name="ACP Agent",
        description="External ACP agent.",
        transport=StdioTransportConfig(command=command, args=args),
    )


def _build_model_config(
    *,
    provider: ProviderType = ProviderType.BIGMODEL,
    model: str = "glm-4.6v",
    base_url: str = "https://open.bigmodel.cn/api/coding/paas/v4",
    api_key: str = "sk-test",
    headers: tuple[ModelRequestHeader, ...] = (),
    context_window: int | None = 128000,
    max_tokens: int = 4096,
) -> ModelEndpointConfig:
    return ModelEndpointConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        headers=headers,
        context_window=context_window,
        sampling=SamplingConfig(max_tokens=max_tokens),
    )


def _build_manager(
    *,
    prompt_text: str,
    workdir: Path,
    config_dir: Path,
    tool_approval_policy: ToolApprovalPolicy | None = None,
    agent: ExternalAgentConfig | None = None,
    resolve_model_config: (
        Callable[[RoleDefinition, LLMRequest], ModelEndpointConfig | None] | None
    ) = None,
) -> ExternalAcpSessionManager:
    return ExternalAcpSessionManager(
        config_dir=config_dir,
        config_service=cast(
            ExternalAgentConfigService, _FakeConfigService(agent or _build_agent())
        ),
        session_repo=cast(ExternalAgentSessionRepository, _FakeSessionRepo()),
        message_repo=cast(MessageRepository, _FakeMessageRepo(prompt_text)),
        run_event_hub=RunEventHub(),
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
        get_mcp_registry=lambda: McpRegistry(),
        get_skill_registry=lambda: cast(SkillRegistry, object()),
        get_role_registry=lambda: cast(RoleRegistry, object()),
        get_task_execution_service=lambda: cast(TaskExecutionService, object()),
        get_task_service=lambda: cast(TaskOrchestrationService, object()),
        run_control_manager=cast(RunControlManager, object()),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=tool_approval_policy or ToolApprovalPolicy(),
        get_notification_service=lambda: cast(NotificationService | None, None),
        resolve_model_config=resolve_model_config,
        im_tool_service=cast(ImToolService | None, None),
    )


def _install_transport_builder(
    *,
    monkeypatch: pytest.MonkeyPatch,
    transport: _RequestCapturingTransport | _DeferredPromptTransport,
    captured: dict[str, object],
) -> None:
    def fake_build_acp_transport(
        *,
        config: ExternalAgentConfig,
        on_message: _TransportMessageHandler,
        runtime_cwd: str | None = None,
    ) -> _RequestCapturingTransport | _DeferredPromptTransport:
        captured["config"] = config
        captured["on_message"] = on_message
        captured["runtime_cwd"] = runtime_cwd
        transport.on_message = on_message
        return transport

    monkeypatch.setattr(
        provider_module,
        "build_acp_transport",
        fake_build_acp_transport,
    )


def test_build_mcp_servers_for_role_ignores_unknown_servers() -> None:
    role = RoleDefinition(
        role_id="writer",
        name="Writer",
        description="Writes documents.",
        version="1.0.0",
        tools=(),
        mcp_servers=("docs", "missing_server"),
        skills=(),
        model_profile="default",
        system_prompt="Write clearly.",
    )
    mcp_registry = McpRegistry(
        (
            McpServerSpec(
                name="docs",
                config={"mcpServers": {"docs": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )

    servers = provider_module._build_mcp_servers_for_role(
        role=role,
        mcp_registry=mcp_registry,
    )

    assert servers == [{"command": "npx", "id": "docs", "name": "docs"}]


def _cast_bridge(bridge: _FakeHostToolBridge) -> ExternalAcpHostToolBridge:
    return cast(ExternalAcpHostToolBridge, bridge)


def _build_handle(
    *,
    transport: _HangingPromptTransport
    | _SequencedPromptTransport
    | _RequestCapturingTransport,
    request: LLMRequest,
) -> _ConversationHandle:
    handle = _ConversationHandle(
        transport=transport,
        external_session_id="external-session-1",
        host_tool_bridge=_cast_bridge(_FakeHostToolBridge(has_tools=False)),
    )
    handle.active_prompt = _ActivePromptState(request=request)
    return handle


_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+c"
    "FfoAAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_external_acp_prompt_includes_system_prompt_and_host_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    bridge = _FakeHostToolBridge(has_tools=True)
    manager = _build_manager(
        prompt_text="Summarize the architecture.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(manager, "_create_host_tool_bridge", lambda: bridge)

    output = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    assert output == "External agent output."
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
async def test_external_acp_prompt_keeps_skill_candidates_in_user_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    manager = _build_manager(
        prompt_text=(
            "Summarize the architecture.\n\n"
            "## Skill Candidates\n"
            "- time: Normalize all times to UTC."
        ),
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(
        manager,
        "_create_host_tool_bridge",
        lambda: _FakeHostToolBridge(has_tools=False),
    )

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    prompt_payload = transport.requests[2][1]
    prompt_parts = cast(list[dict[str, object]], prompt_payload["prompt"])
    prompt_text = str(prompt_parts[0]["text"])
    assert "## Role Prompt\nProvider system prompt text." in prompt_text
    assert "## Skill Candidates" in prompt_text
    assert (
        "## User Prompt\nSummarize the architecture.\n\n## Skill Candidates\n- time: Normalize all times to UTC."
        in prompt_text
    )


@pytest.mark.asyncio
async def test_external_acp_refreshes_remote_session_when_prompt_scoped_mcp_signature_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    bridge = _FakeHostToolBridge(has_tools=True)
    manager = _build_manager(
        prompt_text="Summarize the architecture.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(manager, "_create_host_tool_bridge", lambda: bridge)
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
async def test_external_acp_injects_runtime_model_profile_into_opencode_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    manager = _build_manager(
        prompt_text="Inspect the image.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        agent=_build_agent(command="opencode", args=("--print-logs", "acp")),
        resolve_model_config=lambda _role, _request: _build_model_config(
            model="glm-4v-flash"
        ),
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(
        manager,
        "_create_host_tool_bridge",
        lambda: _FakeHostToolBridge(has_tools=False),
    )

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    runtime_agent = cast(ExternalAgentConfig, captured["config"])
    runtime_transport = runtime_agent.transport
    assert isinstance(runtime_transport, StdioTransportConfig)
    assert runtime_transport.args == ("--print-logs", "acp")
    env_by_name = {binding.name: binding.value for binding in runtime_transport.env}
    assert "OPENCODE_CONFIG_CONTENT" in env_by_name
    assert env_by_name["ZHIPU_API_KEY"] == "sk-test"
    config_content = json.loads(cast(str, env_by_name["OPENCODE_CONFIG_CONTENT"]))
    assert config_content["model"] == "zai/glm-4v-flash"
    provider_config = config_content["provider"]["zai"]
    assert provider_config["npm"] == "@ai-sdk/openai-compatible"
    assert provider_config["api"] == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert provider_config["env"] == ["ZHIPU_API_KEY"]
    model_entry = provider_config["models"]["glm-4v-flash"]
    assert model_entry["attachment"] is True
    assert model_entry["tool_call"] is False
    assert model_entry["modalities"]["input"] == ["text", "image", "video"]
    assert model_entry["limit"] == {"context": 128000, "output": 4096}


@pytest.mark.asyncio
async def test_external_acp_waits_for_trailing_message_chunks_after_prompt_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _DeferredPromptTransport(response_text="Deferred output.")
    captured: dict[str, object] = {}
    manager = _build_manager(
        prompt_text="Answer briefly.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(
        manager,
        "_create_host_tool_bridge",
        lambda: _FakeHostToolBridge(has_tools=False),
    )

    output = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    assert output == "Deferred output."


@pytest.mark.asyncio
async def test_external_acp_synthesizes_opencode_zai_limit_when_context_window_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    manager = _build_manager(
        prompt_text="Inspect the image.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        agent=_build_agent(command="opencode", args=("--print-logs", "acp")),
        resolve_model_config=lambda _role, _request: _build_model_config(
            context_window=None
        ),
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(
        manager,
        "_create_host_tool_bridge",
        lambda: _FakeHostToolBridge(has_tools=False),
    )

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    runtime_agent = cast(ExternalAgentConfig, captured["config"])
    runtime_transport = runtime_agent.transport
    assert isinstance(runtime_transport, StdioTransportConfig)
    env_by_name = {binding.name: binding.value for binding in runtime_transport.env}
    config_content = json.loads(cast(str, env_by_name["OPENCODE_CONFIG_CONTENT"]))
    model_entry = config_content["provider"]["zai"]["models"]["glm-4.6v"]
    assert model_entry["limit"] == {"context": 128000, "output": 4096}


@pytest.mark.asyncio
async def test_external_acp_falls_back_to_custom_provider_for_generic_openai_compatible_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    manager = _build_manager(
        prompt_text="Answer briefly.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        agent=_build_agent(command="opencode", args=("acp",)),
        resolve_model_config=lambda _role, _request: _build_model_config(
            provider=ProviderType.OPENAI_COMPATIBLE,
            model="gpt-4o-mini",
            base_url="https://example.test/v1",
        ),
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(
        manager,
        "_create_host_tool_bridge",
        lambda: _FakeHostToolBridge(has_tools=False),
    )

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    runtime_agent = cast(ExternalAgentConfig, captured["config"])
    runtime_transport = runtime_agent.transport
    assert isinstance(runtime_transport, StdioTransportConfig)
    env_by_name = {binding.name: binding.value for binding in runtime_transport.env}
    assert env_by_name["AGENT_TEAMS_OPENCODE_API_KEY"] == "sk-test"
    config_content = json.loads(cast(str, env_by_name["OPENCODE_CONFIG_CONTENT"]))
    assert config_content["model"] == "agent_teams/gpt-4o-mini"
    provider_config = config_content["provider"]["agent_teams"]
    assert provider_config["api"] == "https://example.test/v1"
    assert provider_config["env"] == ["AGENT_TEAMS_OPENCODE_API_KEY"]
    assert provider_config["npm"] == "@ai-sdk/openai-compatible"
    assert provider_config["models"]["gpt-4o-mini"]["name"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_external_acp_injects_custom_headers_into_opencode_provider_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    manager = _build_manager(
        prompt_text="Answer briefly.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        agent=_build_agent(command="opencode", args=("acp",)),
        resolve_model_config=lambda _role, _request: _build_model_config(
            provider=ProviderType.OPENAI_COMPATIBLE,
            model="gpt-4o-mini",
            base_url="https://example.test/v1",
            api_key="sk-ignored",
            headers=(
                ModelRequestHeader(
                    name="Authorization",
                    value="Bearer header-override",
                ),
                ModelRequestHeader(
                    name="anthropic-version",
                    value="2023-06-01",
                ),
            ),
        ),
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(
        manager,
        "_create_host_tool_bridge",
        lambda: _FakeHostToolBridge(has_tools=False),
    )

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    runtime_agent = cast(ExternalAgentConfig, captured["config"])
    runtime_transport = runtime_agent.transport
    assert isinstance(runtime_transport, StdioTransportConfig)
    env_by_name = {binding.name: binding.value for binding in runtime_transport.env}
    assert "AGENT_TEAMS_OPENCODE_API_KEY" not in env_by_name
    config_content = json.loads(cast(str, env_by_name["OPENCODE_CONFIG_CONTENT"]))
    provider_config = config_content["provider"]["agent_teams"]
    assert "env" not in provider_config
    assert provider_config["options"]["headers"] == {
        "Authorization": "Bearer header-override",
        "anthropic-version": "2023-06-01",
    }


@pytest.mark.asyncio
async def test_external_acp_omits_custom_provider_limit_when_context_window_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    manager = _build_manager(
        prompt_text="Answer briefly.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        agent=_build_agent(command="opencode", args=("acp",)),
        resolve_model_config=lambda _role, _request: _build_model_config(
            provider=ProviderType.OPENAI_COMPATIBLE,
            model="gpt-4o-mini",
            base_url="https://example.test/v1",
            context_window=None,
        ),
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(
        manager,
        "_create_host_tool_bridge",
        lambda: _FakeHostToolBridge(has_tools=False),
    )

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    runtime_agent = cast(ExternalAgentConfig, captured["config"])
    runtime_transport = runtime_agent.transport
    assert isinstance(runtime_transport, StdioTransportConfig)
    env_by_name = {binding.name: binding.value for binding in runtime_transport.env}
    config_content = json.loads(cast(str, env_by_name["OPENCODE_CONFIG_CONTENT"]))
    model_entry = config_content["provider"]["agent_teams"]["models"]["gpt-4o-mini"]
    assert "limit" not in model_entry


@pytest.mark.asyncio
async def test_external_acp_recreates_opencode_transport_when_model_profile_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transports = [_RequestCapturingTransport(), _RequestCapturingTransport()]
    captured_configs: list[ExternalAgentConfig] = []
    model_state = {"config": _build_model_config(model="glm-4.6v")}
    manager = _build_manager(
        prompt_text="Inspect the image.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        agent=_build_agent(command="opencode", args=("acp",)),
        resolve_model_config=lambda _role, _request: model_state["config"],
    )

    def fake_build_acp_transport(
        *,
        config: ExternalAgentConfig,
        on_message: _TransportMessageHandler,
        runtime_cwd: str | None = None,
    ) -> _RequestCapturingTransport:
        _ = on_message
        _ = runtime_cwd
        captured_configs.append(config)
        transport = transports[len(captured_configs) - 1]
        transport.on_message = on_message
        return transport

    monkeypatch.setattr(
        provider_module,
        "build_acp_transport",
        fake_build_acp_transport,
    )
    monkeypatch.setattr(
        manager,
        "_create_host_tool_bridge",
        lambda: _FakeHostToolBridge(has_tools=False),
    )

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )

    model_state["config"] = _build_model_config(model="glm-5")
    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request().model_copy(
            update={"run_id": "run-2", "task_id": "task-2"}
        ),
    )

    assert len(captured_configs) == 2
    first_transport = cast(StdioTransportConfig, captured_configs[0].transport)
    second_transport = cast(StdioTransportConfig, captured_configs[1].transport)
    assert first_transport.args == ("acp",)
    assert second_transport.args == ("acp",)
    first_env = {binding.name: binding.value for binding in first_transport.env}
    second_env = {binding.name: binding.value for binding in second_transport.env}
    first_config = json.loads(cast(str, first_env["OPENCODE_CONFIG_CONTENT"]))
    second_config = json.loads(cast(str, second_env["OPENCODE_CONFIG_CONTENT"]))
    assert first_config["model"] == "zai/glm-4.6v"
    assert second_config["model"] == "zai/glm-5"
    assert transports[0].close_calls == 1


@pytest.mark.asyncio
async def test_external_acp_routes_mcp_callbacks_to_host_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = _RequestCapturingTransport()
    captured: dict[str, object] = {}
    bridge = _FakeHostToolBridge(has_tools=True)
    manager = _build_manager(
        prompt_text="Summarize the architecture.",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )
    _install_transport_builder(
        monkeypatch=monkeypatch,
        transport=transport,
        captured=captured,
    )
    monkeypatch.setattr(manager, "_create_host_tool_bridge", lambda: bridge)

    _ = await manager.prompt(
        agent_id="agent-1",
        role=_build_role(),
        request=_build_request(),
    )
    on_message = cast(_TransportMessageHandler, captured["on_message"])

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


@pytest.mark.asyncio
async def test_agent_message_chunk_converts_png_content_to_data_url(
    tmp_path: Path,
) -> None:
    manager = _build_manager(
        prompt_text="return the image",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="agent-1",
    )
    handle = _build_handle(
        transport=_RequestCapturingTransport(),
        request=request,
    )
    manager._conversations[key] = handle

    queue = manager._run_event_hub.subscribe(request.run_id)
    await manager._handle_transport_message(
        key=key,
        method="session/update",
        params={
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": "iVBORw0KGgoAAAANSUhEUgAAAAUA",
                },
            }
        },
        message_id=None,
    )

    expected = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA"
    assert handle.active_prompt is not None
    assert handle.active_prompt.text_chunks == [expected]

    event = queue.get_nowait()
    assert event.event_type == RunEventType.TEXT_DELTA
    payload = json.loads(event.payload_json)
    assert payload["text"] == expected


def test_extract_tool_result_keeps_text_json_behavior() -> None:
    result = _extract_tool_result(
        {
            "content": [
                {
                    "type": "content",
                    "content": {
                        "type": "text",
                        "text": '{"ok": true}',
                    },
                }
            ]
        }
    )

    assert result == {"ok": True}


def test_extract_tool_result_converts_image_content_to_text_data_url() -> None:
    result = _extract_tool_result(
        {
            "content": [
                {
                    "type": "content",
                    "content": {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": "aGVsbG8=",
                    },
                }
            ]
        }
    )

    assert result == {"text": "data:image/png;base64,aGVsbG8="}


def test_annotate_external_computer_tool_result_wraps_known_desktop_tools() -> None:
    result = _annotate_external_computer_tool_result(
        tool_name="press_key",
        tool_result={
            "text": "Pressed Enter.",
            "content": [
                {
                    "kind": "media_ref",
                    "asset_id": "asset-1",
                    "session_id": "session-1",
                    "modality": "image",
                    "mime_type": "image/png",
                    "url": "/api/sessions/session-1/media/asset-1/file",
                }
            ],
            "observation": {"focused_window": "Chrome DevTools"},
        },
    )

    assert isinstance(result, dict)
    computer = result["computer"]
    assert isinstance(computer, dict)
    assert computer["source"] == "acp"
    assert computer["runtime_kind"] == "external_acp"
    assert result["text"] == "Pressed Enter."


@pytest.mark.asyncio
async def test_prompt_uses_image_tool_result_as_timeout_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _build_manager(
        prompt_text="return the image",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        tool_approval_policy=ToolApprovalPolicy(timeout_seconds=0.01),
    )
    transport = _HangingPromptTransport()
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="opencode",
    )
    handle = _ConversationHandle(
        transport=transport,
        external_session_id="external-session-1",
        host_tool_bridge=_cast_bridge(_FakeHostToolBridge(has_tools=False)),
    )
    manager._conversations[key] = handle
    queue = manager._run_event_hub.subscribe(request.run_id)

    async def _ensure_conversation(**_: object) -> _ConversationHandle:
        return handle

    monkeypatch.setattr(manager, "_ensure_conversation", _ensure_conversation)
    monkeypatch.setattr(
        provider_module,
        "_EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS",
        0.01,
    )

    prompt_task = asyncio.create_task(
        manager.prompt(
            agent_id="agent-1",
            role=_build_role(),
            request=request,
        )
    )
    await transport.prompt_started.wait()
    await manager._handle_transport_message(
        key=key,
        method="session/update",
        params={
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tool-1",
                "title": "bash",
                "content": [
                    {
                        "type": "content",
                        "content": {
                            "type": "text",
                            "text": _PNG_BASE64,
                        },
                    }
                ],
            }
        },
        message_id=None,
    )

    result = await prompt_task

    assert result == f"data:image/png;base64,{_PNG_BASE64}"
    assert transport.notifications == [
        ("session/cancel", {"sessionId": "external-session-1"})
    ]

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    text_events = [
        event for event in events if event.event_type == RunEventType.TEXT_DELTA
    ]
    assert text_events[-1].payload_json == json.dumps(
        {
            "text": result,
            "role_id": request.role_id,
            "instance_id": request.instance_id,
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_prompt_times_out_when_external_agent_stops_sending_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _build_manager(
        prompt_text="return the image",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        tool_approval_policy=ToolApprovalPolicy(timeout_seconds=0.01),
    )
    transport = _HangingPromptTransport()
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="agent-1",
    )
    handle = _ConversationHandle(
        transport=transport,
        external_session_id="external-session-1",
        host_tool_bridge=_cast_bridge(_FakeHostToolBridge(has_tools=False)),
    )
    manager._conversations[key] = handle

    async def _ensure_conversation(**_: object) -> _ConversationHandle:
        return handle

    monkeypatch.setattr(manager, "_ensure_conversation", _ensure_conversation)
    monkeypatch.setattr(
        provider_module,
        "_EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS",
        0.01,
    )

    with pytest.raises(
        RuntimeError,
        match="External ACP prompt timed out after 0.01 seconds without updates",
    ):
        await manager.prompt(
            agent_id="agent-1",
            role=_build_role(),
            request=request,
        )

    assert transport.notifications == [
        ("session/cancel", {"sessionId": "external-session-1"})
    ]


@pytest.mark.asyncio
async def test_prompt_waits_for_tool_approval_timeout_before_failing_on_inactivity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _build_manager(
        prompt_text="return the image",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
        tool_approval_policy=ToolApprovalPolicy(timeout_seconds=0.05),
    )
    transport = _SequencedPromptTransport()
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="agent-1",
    )
    handle = _ConversationHandle(
        transport=transport,
        external_session_id="external-session-1",
        host_tool_bridge=_cast_bridge(_FakeHostToolBridge(has_tools=False)),
    )
    manager._conversations[key] = handle

    async def _ensure_conversation(**_: object) -> _ConversationHandle:
        return handle

    monkeypatch.setattr(manager, "_ensure_conversation", _ensure_conversation)
    monkeypatch.setattr(
        provider_module,
        "_EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS",
        0.01,
    )

    prompt_task = asyncio.create_task(
        manager.prompt(
            agent_id="agent-1",
            role=_build_role(),
            request=request,
        )
    )

    first_attempt = await transport.prompt_started.get()
    assert first_attempt == 1
    await asyncio.sleep(0.02)
    await manager._handle_transport_message(
        key=key,
        method="session/update",
        params={
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {
                    "type": "text",
                    "text": "delayed output",
                },
            }
        },
        message_id=None,
    )
    transport.gates[0].set()

    result = await prompt_task

    assert result == "delayed output"


@pytest.mark.asyncio
async def test_prompt_retries_once_when_external_agent_returns_empty_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _build_manager(
        prompt_text="return the image",
        workdir=tmp_path,
        config_dir=tmp_path / "config",
    )
    transport = _SequencedPromptTransport()
    request = _build_request()
    key = _conversation_key(
        session_id=request.session_id,
        role_id=request.role_id,
        agent_id="agent-1",
    )
    handle = _ConversationHandle(
        transport=transport,
        external_session_id="external-session-1",
        host_tool_bridge=_cast_bridge(_FakeHostToolBridge(has_tools=False)),
    )
    manager._conversations[key] = handle

    async def _ensure_conversation(**_: object) -> _ConversationHandle:
        return handle

    monkeypatch.setattr(manager, "_ensure_conversation", _ensure_conversation)
    prompt_task = asyncio.create_task(
        manager.prompt(
            agent_id="agent-1",
            role=_build_role(),
            request=request,
        )
    )

    first_attempt = await transport.prompt_started.get()
    assert first_attempt == 1
    transport.gates[0].set()

    second_attempt = await transport.prompt_started.get()
    assert second_attempt == 2
    await manager._handle_transport_message(
        key=key,
        method="session/update",
        params={
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {
                    "type": "text",
                    "text": "data:image/png;base64,aGVsbG8=",
                },
            }
        },
        message_id=None,
    )
    transport.gates[1].set()

    result = await prompt_task

    assert result == "data:image/png;base64,aGVsbG8="
    assert len(transport.requests) == 2
    second_prompt = cast(
        list[dict[str, JsonValue]],
        transport.requests[1]["prompt"],
    )
    second_prompt_text = cast(str, second_prompt[0]["text"])
    assert "## Role Prompt" in second_prompt_text
    assert "Your previous reply was empty." in second_prompt_text
