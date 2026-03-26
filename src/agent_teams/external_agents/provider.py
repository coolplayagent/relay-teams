# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
from pydantic_ai.messages import UserPromptPart

from agent_teams.external_agents.acp_client import (
    AcpProtocolError,
    AcpTransportClient,
    build_acp_transport,
)
from agent_teams.external_agents.config_service import ExternalAgentConfigService
from agent_teams.external_agents.host_tool_bridge import (
    HOST_TOOL_SERVER_ID,
    ExternalAcpHostToolBridge,
)
from agent_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentSessionRecord,
    ExternalAgentSessionStatus,
)
from agent_teams.external_agents.session_repository import (
    ExternalAgentSessionRepository,
)
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.providers.provider_contracts import LLMProvider, LLMRequest
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.workspace import WorkspaceManager

if TYPE_CHECKING:
    from agent_teams.agents.execution.message_repository import MessageRepository
    from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
    from agent_teams.agents.orchestration.task_execution_service import (
        TaskExecutionService,
    )
    from agent_teams.agents.orchestration.task_orchestration_service import (
        TaskOrchestrationService,
    )
    from agent_teams.agents.tasks.task_repository import TaskRepository
    from agent_teams.metrics import MetricRecorder
    from agent_teams.notifications import NotificationService
    from agent_teams.persistence.shared_state_repo import SharedStateRepository
    from agent_teams.roles.memory_service import RoleMemoryService
    from agent_teams.roles.role_registry import RoleRegistry
    from agent_teams.sessions.runs.event_log import EventLog
    from agent_teams.sessions.runs.injection_queue import RunInjectionManager
    from agent_teams.sessions.runs.run_control_manager import RunControlManager
    from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
    from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
    from agent_teams.skills.skill_registry import SkillRegistry
    from agent_teams.tools.feishu_tools import FeishuToolService
    from agent_teams.tools.registry import ToolRegistry
    from agent_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
    from agent_teams.tools.runtime.approval_ticket_repo import (
        ApprovalTicketRepository,
    )


class ExternalAcpProvider(LLMProvider):
    def __init__(
        self,
        *,
        role: RoleDefinition,
        session_manager: ExternalAcpSessionManager,
    ) -> None:
        self._role = role
        self._session_manager = session_manager

    async def generate(self, request: LLMRequest) -> str:
        if not self._role.bound_agent_id:
            raise RuntimeError(
                f"Role {self._role.role_id} is not bound to an external agent"
            )
        return await self._session_manager.prompt(
            agent_id=self._role.bound_agent_id,
            role=self._role,
            request=request,
        )


class ExternalAcpSessionManager:
    def __init__(
        self,
        *,
        config_dir: Path,
        config_service: ExternalAgentConfigService,
        session_repo: ExternalAgentSessionRepository,
        message_repo: MessageRepository,
        run_event_hub: RunEventHub,
        workspace_manager: WorkspaceManager,
        task_repo: TaskRepository,
        shared_store: SharedStateRepository,
        event_bus: EventLog,
        injection_manager: RunInjectionManager,
        agent_repo: AgentInstanceRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        run_runtime_repo: RunRuntimeRepository,
        run_intent_repo: RunIntentRepository,
        role_memory_service: RoleMemoryService | None,
        tool_registry: ToolRegistry,
        get_mcp_registry: Callable[[], McpRegistry],
        get_skill_registry: Callable[[], SkillRegistry],
        get_role_registry: Callable[[], RoleRegistry],
        get_task_execution_service: Callable[[], TaskExecutionService],
        get_task_service: Callable[[], TaskOrchestrationService],
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        tool_approval_policy: ToolApprovalPolicy,
        get_notification_service: Callable[[], NotificationService | None],
        metric_recorder: MetricRecorder | None = None,
        feishu_tool_service: FeishuToolService | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._config_service = config_service
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._run_event_hub = run_event_hub
        self._workspace_manager = workspace_manager
        self._task_repo = task_repo
        self._shared_store = shared_store
        self._event_bus = event_bus
        self._injection_manager = injection_manager
        self._agent_repo = agent_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._run_runtime_repo = run_runtime_repo
        self._run_intent_repo = run_intent_repo
        self._role_memory_service = role_memory_service
        self._tool_registry = tool_registry
        self._get_mcp_registry = get_mcp_registry
        self._get_skill_registry = get_skill_registry
        self._get_role_registry = get_role_registry
        self._get_task_execution_service = get_task_execution_service
        self._get_task_service = get_task_service
        self._run_control_manager = run_control_manager
        self._tool_approval_manager = tool_approval_manager
        self._tool_approval_policy = tool_approval_policy
        self._get_notification_service = get_notification_service
        self._metric_recorder = metric_recorder
        self._feishu_tool_service = feishu_tool_service
        self._conversations: dict[str, _ConversationHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def prompt(
        self,
        *,
        agent_id: str,
        role: RoleDefinition,
        request: LLMRequest,
    ) -> str:
        key = _conversation_key(
            session_id=request.session_id,
            role_id=request.role_id,
            agent_id=agent_id,
        )
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            agent = self._config_service.resolve_runtime_agent(agent_id)
            handle = await self._ensure_conversation(
                key=key,
                agent=agent,
                role=role,
                request=request,
            )
            prompt_text = _extract_latest_user_prompt(
                self._message_repo.get_history_for_conversation_task(
                    request.conversation_id,
                    request.task_id,
                )
            )
            if prompt_text is None:
                prompt_text = str(request.user_prompt or "").strip()
            if not prompt_text:
                raise RuntimeError(
                    "External ACP prompt could not resolve a user message for the task"
                )
            composed_prompt = _compose_external_prompt(
                system_prompt=request.system_prompt,
                user_prompt=prompt_text,
                include_host_tool_guidance=handle.host_tool_bridge.has_tools(),
            )
            state = _ActivePromptState(request=request)
            handle.active_prompt = state
            handle.host_tool_bridge.bind_active_request(request)
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.MODEL_STEP_STARTED,
                    payload_json=json.dumps(
                        {
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            try:
                _ = await handle.transport.send_request(
                    "session/prompt",
                    {
                        "sessionId": handle.external_session_id,
                        "messageId": f"{request.run_id}:{request.task_id}",
                        "prompt": [{"type": "text", "text": composed_prompt}],
                    },
                )
            except asyncio.CancelledError:
                await self._cancel_prompt(handle)
                raise
            finally:
                if state.thinking_started:
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.THINKING_FINISHED,
                            payload_json=json.dumps(
                                {
                                    "part_index": 0,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                },
                                ensure_ascii=False,
                            ),
                        )
                    )
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=request.session_id,
                        run_id=request.run_id,
                        trace_id=request.trace_id,
                        task_id=request.task_id,
                        instance_id=request.instance_id,
                        role_id=request.role_id,
                        event_type=RunEventType.MODEL_STEP_FINISHED,
                        payload_json=json.dumps(
                            {
                                "role_id": request.role_id,
                                "instance_id": request.instance_id,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                handle.active_prompt = None
                handle.host_tool_bridge.clear_active_request()
            output = "".join(state.text_chunks).strip()
            if output:
                self._append_assistant_message(
                    request=request,
                    content=output,
                )
            return output

    async def close(self) -> None:
        handles = list(self._conversations.values())
        self._conversations.clear()
        for handle in handles:
            await handle.close()

    async def _ensure_conversation(
        self,
        *,
        key: str,
        agent: ExternalAgentConfig,
        role: RoleDefinition,
        request: LLMRequest,
    ) -> _ConversationHandle:
        existing = self._conversations.get(key)
        if existing is not None:
            await existing.transport.start()
            await self._refresh_remote_session_if_needed(
                handle=existing,
                role=role,
                request=request,
                agent=agent,
            )
            return existing

        async def on_message(
            method: str,
            params: dict[str, JsonValue],
            message_id: str | int | None,
        ) -> dict[str, JsonValue]:
            return await self._handle_transport_message(
                key=key,
                method=method,
                params=params,
                message_id=message_id,
            )

        workspace = self._resolve_workspace(request)
        transport = build_acp_transport(
            config=agent,
            on_message=on_message,
            runtime_cwd=str(workspace.resolve_workdir()),
        )
        await transport.start()
        _ = await transport.send_request("initialize", {"protocolVersion": 1})
        handle = _ConversationHandle(
            transport=transport,
            external_session_id="",
            host_tool_bridge=self._create_host_tool_bridge(),
        )
        self._conversations[key] = handle
        persisted = self._session_repo.get(
            session_id=request.session_id,
            role_id=request.role_id,
            agent_id=agent.agent_id,
        )
        session_params = await self._build_session_params(
            handle=handle,
            role=role,
            request=request,
        )
        handle.external_session_id = await self._load_or_create_remote_session(
            transport=transport,
            persisted=persisted,
            session_params=session_params,
        )
        handle.session_signature = _session_signature(session_params)
        _ = await handle.host_tool_bridge.configure(
            role=role,
            session_id=request.session_id,
            external_session_id=handle.external_session_id,
            send_request=transport.send_request,
            send_notification=transport.send_notification,
        )
        self._persist_session_record(
            request=request,
            agent=agent,
            external_session_id=handle.external_session_id,
        )
        return handle

    async def _load_or_create_remote_session(
        self,
        *,
        transport: AcpTransportClient,
        persisted: ExternalAgentSessionRecord | None,
        session_params: dict[str, JsonValue],
    ) -> str:
        if persisted is not None:
            try:
                result = await transport.send_request(
                    "session/load",
                    {
                        "sessionId": persisted.external_session_id,
                        **session_params,
                    },
                )
                loaded_session_id = _required_str(result, "sessionId")
                return loaded_session_id
            except Exception:
                self._session_repo.delete(
                    session_id=persisted.session_id,
                    role_id=persisted.role_id,
                    agent_id=persisted.agent_id,
                )
        created = await transport.send_request("session/new", session_params)
        return _required_str(created, "sessionId")

    async def _refresh_remote_session_if_needed(
        self,
        *,
        handle: _ConversationHandle,
        role: RoleDefinition,
        request: LLMRequest,
        agent: ExternalAgentConfig,
    ) -> None:
        session_params = await self._build_session_params(
            handle=handle,
            role=role,
            request=request,
        )
        signature = _session_signature(session_params)
        if handle.external_session_id and signature == handle.session_signature:
            return
        handle.external_session_id = await self._reload_remote_session(
            transport=handle.transport,
            external_session_id=handle.external_session_id,
            session_params=session_params,
        )
        handle.session_signature = signature
        _ = await handle.host_tool_bridge.configure(
            role=role,
            session_id=request.session_id,
            external_session_id=handle.external_session_id,
            send_request=handle.transport.send_request,
            send_notification=handle.transport.send_notification,
        )
        self._persist_session_record(
            request=request,
            agent=agent,
            external_session_id=handle.external_session_id,
        )

    async def _reload_remote_session(
        self,
        *,
        transport: AcpTransportClient,
        external_session_id: str,
        session_params: dict[str, JsonValue],
    ) -> str:
        if external_session_id:
            try:
                result = await transport.send_request(
                    "session/load",
                    {
                        "sessionId": external_session_id,
                        **session_params,
                    },
                )
                return _required_str(result, "sessionId")
            except Exception:
                pass
        created = await transport.send_request("session/new", session_params)
        return _required_str(created, "sessionId")

    async def _build_session_params(
        self,
        *,
        handle: _ConversationHandle,
        role: RoleDefinition,
        request: LLMRequest,
    ) -> dict[str, JsonValue]:
        _ = await handle.host_tool_bridge.configure(
            role=role,
            session_id=request.session_id,
            external_session_id=handle.external_session_id,
            send_request=handle.transport.send_request,
            send_notification=handle.transport.send_notification,
        )
        workspace = self._resolve_workspace(request)
        mcp_servers = cast(
            JsonValue,
            _build_mcp_servers_for_role(
                role=role,
                mcp_registry=self._get_mcp_registry(),
                host_server=handle.host_tool_bridge.stdio_server_payload(
                    config_dir=self._config_dir,
                    request=request,
                ),
            ),
        )
        return {
            "cwd": str(workspace.resolve_workdir()),
            "mcpServers": mcp_servers,
        }

    async def _handle_transport_message(
        self,
        *,
        key: str,
        method: str,
        params: dict[str, JsonValue],
        message_id: str | int | None,
    ) -> dict[str, JsonValue]:
        if method == "initialized":
            return {}
        if method == "session/update":
            self._handle_session_update(
                key=key,
                params=params,
            )
            return {}
        handle = self._conversations.get(key)
        if handle is None:
            raise AcpProtocolError(-32000, f"Unknown external ACP conversation: {key}")
        self._validate_session_id(
            handle=handle,
            params=params,
        )
        if method == "mcp/connect":
            server_id = _required_str(params, "acpId", fallback_key="serverId")
            if server_id != HOST_TOOL_SERVER_ID:
                raise AcpProtocolError(
                    -32602,
                    f"Unknown host MCP server_id: {server_id}",
                )
            try:
                return await handle.host_tool_bridge.open_connection(
                    server_id=server_id
                )
            except KeyError as exc:
                raise AcpProtocolError(-32602, str(exc)) from exc
        if method == "mcp/message":
            connection_id = _required_str(params, "connectionId")
            try:
                return await handle.host_tool_bridge.relay_message(
                    connection_id=connection_id,
                    method=_required_str(params, "method"),
                    params=_as_object(params.get("params")),
                    message_id=message_id,
                )
            except KeyError as exc:
                raise AcpProtocolError(-32602, str(exc)) from exc
        if method == "mcp/disconnect":
            connection_id = _required_str(params, "connectionId")
            try:
                return await handle.host_tool_bridge.close_connection(
                    connection_id=connection_id
                )
            except KeyError as exc:
                raise AcpProtocolError(-32602, str(exc)) from exc
        raise AcpProtocolError(-32601, f"Method not found: {method}")

    def _handle_session_update(
        self,
        *,
        key: str,
        params: dict[str, JsonValue],
    ) -> None:
        update = _as_object(params.get("update"))
        handle = self._conversations.get(key)
        if handle is None or handle.active_prompt is None:
            return
        request = handle.active_prompt.request
        update_name = _required_str(update, "sessionUpdate")
        if update_name == "agent_message_chunk":
            content = _as_object(update.get("content"))
            text = _optional_str(content.get("text"))
            if not text:
                return
            handle.active_prompt.text_chunks.append(text)
            self._publish_text_delta(request=request, text=text)
            return
        if update_name == "agent_thought_chunk":
            content = _as_object(update.get("content"))
            text = _optional_str(content.get("text"))
            if not text:
                return
            if not handle.active_prompt.thinking_started:
                handle.active_prompt.thinking_started = True
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=request.session_id,
                        run_id=request.run_id,
                        trace_id=request.trace_id,
                        task_id=request.task_id,
                        instance_id=request.instance_id,
                        role_id=request.role_id,
                        event_type=RunEventType.THINKING_STARTED,
                        payload_json=json.dumps(
                            {
                                "part_index": 0,
                                "role_id": request.role_id,
                                "instance_id": request.instance_id,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.THINKING_DELTA,
                    payload_json=json.dumps(
                        {
                            "part_index": 0,
                            "text": text,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            return
        if update_name == "tool_call":
            payload = {
                "tool_call_id": update.get("toolCallId"),
                "tool_name": _optional_str(update.get("title")) or "tool",
            }
            raw_input = update.get("rawInput")
            if raw_input is not None:
                payload["args"] = raw_input
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.TOOL_CALL,
                    payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                )
            )
            return
        if update_name == "tool_call_update":
            tool_result = _extract_tool_result(update)
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.TOOL_RESULT,
                    payload_json=json.dumps(
                        {
                            "tool_call_id": update.get("toolCallId"),
                            "tool_name": _optional_str(update.get("title")) or "tool",
                            "result": tool_result,
                            "error": _optional_str(update.get("status")) == "failed",
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
            )

    def _resolve_workspace(self, request: LLMRequest):
        return self._workspace_manager.resolve(
            session_id=request.session_id,
            role_id=request.role_id,
            instance_id=request.instance_id,
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
        )

    def _persist_session_record(
        self,
        *,
        request: LLMRequest,
        agent: ExternalAgentConfig,
        external_session_id: str,
    ) -> None:
        self._session_repo.upsert(
            ExternalAgentSessionRecord(
                session_id=request.session_id,
                role_id=request.role_id,
                agent_id=agent.agent_id,
                transport=agent.transport.transport,
                external_session_id=external_session_id,
                status=ExternalAgentSessionStatus.READY,
            )
        )

    def _validate_session_id(
        self,
        *,
        handle: _ConversationHandle,
        params: dict[str, JsonValue],
    ) -> None:
        session_id = _optional_str(params.get("sessionId"))
        if session_id is None:
            return
        if session_id != handle.external_session_id:
            raise AcpProtocolError(
                -32602,
                f"Unknown external ACP sessionId: {session_id}",
            )

    def _create_host_tool_bridge(self) -> ExternalAcpHostToolBridge:
        return ExternalAcpHostToolBridge(
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            event_bus=self._event_bus,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            agent_repo=self._agent_repo,
            approval_ticket_repo=self._approval_ticket_repo,
            run_runtime_repo=self._run_runtime_repo,
            run_intent_repo=self._run_intent_repo,
            workspace_manager=self._workspace_manager,
            role_memory_service=self._role_memory_service,
            tool_registry=self._tool_registry,
            message_repo=self._message_repo,
            get_mcp_registry=self._get_mcp_registry,
            get_skill_registry=self._get_skill_registry,
            get_role_registry=self._get_role_registry,
            get_task_execution_service=self._get_task_execution_service,
            get_task_service=self._get_task_service,
            run_control_manager=self._run_control_manager,
            tool_approval_manager=self._tool_approval_manager,
            tool_approval_policy=self._tool_approval_policy,
            get_notification_service=self._get_notification_service,
            metric_recorder=self._metric_recorder,
            feishu_tool_service=self._feishu_tool_service,
        )

    async def _cancel_prompt(self, handle: _ConversationHandle) -> None:
        await handle.transport.send_notification(
            "session/cancel",
            {"sessionId": handle.external_session_id},
        )

    def _publish_text_delta(self, *, request: LLMRequest, text: str) -> None:
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.TEXT_DELTA,
                payload_json=json.dumps(
                    {
                        "text": text,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                    },
                    ensure_ascii=False,
                ),
            )
        )

    def _append_assistant_message(
        self,
        *,
        request: LLMRequest,
        content: str,
    ) -> None:
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[
                ModelResponse(
                    parts=[TextPart(content=content)],
                    timestamp=datetime.now(tz=timezone.utc),
                )
            ],
        )


class _ConversationHandle:
    def __init__(
        self,
        *,
        transport: AcpTransportClient,
        external_session_id: str,
        host_tool_bridge: ExternalAcpHostToolBridge,
    ) -> None:
        self.transport = transport
        self.external_session_id = external_session_id
        self.host_tool_bridge = host_tool_bridge
        self.session_signature = ""
        self.active_prompt: _ActivePromptState | None = None

    async def close(self) -> None:
        await self.host_tool_bridge.close()
        await self.transport.close()


class _ActivePromptState:
    def __init__(self, *, request: LLMRequest) -> None:
        self.request = request
        self.text_chunks: list[str] = []
        self.thinking_started = False


def _conversation_key(*, session_id: str, role_id: str, agent_id: str) -> str:
    return f"{session_id}:{role_id}:{agent_id}"


def _build_mcp_servers_for_role(
    *,
    role: RoleDefinition,
    mcp_registry: McpRegistry,
    host_server: dict[str, JsonValue] | None = None,
) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    for server_name in role.mcp_servers:
        if server_name == HOST_TOOL_SERVER_ID:
            raise RuntimeError(
                f"MCP server id {HOST_TOOL_SERVER_ID} is reserved for Agent Teams "
                "host tools."
            )
        spec = mcp_registry.get_spec(server_name)
        payload = {str(key): value for key, value in spec.server_config.items()}
        payload["id"] = server_name
        payload["name"] = server_name
        result.append(payload)
    if host_server is not None:
        result.append(dict(host_server))
    return result


def _extract_latest_user_prompt(
    history: Sequence[ModelMessage],
) -> str | None:
    for message in reversed(history):
        if not isinstance(message, ModelRequest):
            continue
        prompt_parts = [
            part for part in message.parts if isinstance(part, UserPromptPart)
        ]
        if len(prompt_parts) != len(message.parts):
            continue
        combined = "\n".join(
            str(part.content or "").strip() for part in prompt_parts
        ).strip()
        if combined:
            return combined
    return None


def _extract_tool_result(update: dict[str, JsonValue]) -> JsonValue:
    content = update.get("content")
    if not isinstance(content, list) or not content:
        return {}
    first_item = content[0]
    if not isinstance(first_item, dict):
        return {}
    inner_content = _as_object(first_item.get("content"))
    text = _optional_str(inner_content.get("text"))
    if not text:
        return {}
    try:
        return cast(JsonValue, json.loads(text))
    except json.JSONDecodeError:
        return {"text": text}


def _as_object(value: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _compose_external_prompt(
    *,
    system_prompt: str,
    user_prompt: str,
    include_host_tool_guidance: bool,
) -> str:
    sections = [
        f"## Role Prompt\n{system_prompt.strip()}",
    ]
    if include_host_tool_guidance:
        sections.append(
            "## Host Tools\n"
            "When interacting with Agent Teams session state, workspace state, "
            "tasks, approvals, or skills, prefer the `agent_teams_*` host tools "
            "over similarly named native tools."
        )
    sections.append(f"## User Prompt\n{user_prompt.strip()}")
    return "\n\n".join(section for section in sections if section.strip())


def _session_signature(payload: dict[str, JsonValue]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _required_str(
    payload: dict[str, JsonValue],
    key: str,
    *,
    fallback_key: str | None = None,
) -> str:
    value = _optional_str(payload.get(key))
    if value is None and fallback_key is not None:
        value = _optional_str(payload.get(fallback_key))
    if value is None:
        raise RuntimeError(f"{key} must be a non-empty string")
    return value


def _optional_str(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
