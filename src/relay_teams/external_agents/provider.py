# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
from pydantic_ai.messages import UserPromptPart

from relay_teams.computer import (
    ComputerRuntime,
    build_computer_tool_payload,
    describe_external_acp_tool,
)
from relay_teams.external_agents.acp_client import (
    AcpProtocolError,
    AcpTransportClient,
    build_acp_transport,
)
from relay_teams.external_agents.config_service import ExternalAgentConfigService
from relay_teams.external_agents.host_tool_bridge import (
    HOST_TOOL_SERVER_ID,
    ExternalAcpHostToolBridge,
)
from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentSecretBinding,
    ExternalAgentSessionRecord,
    ExternalAgentSessionStatus,
    StdioTransportConfig,
)
from relay_teams.external_agents.session_repository import (
    ExternalAgentSessionRepository,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.media import MediaAssetService
from relay_teams.monitors import MonitorService
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.providers.model_config import ModelEndpointConfig, ProviderType
from relay_teams.providers.openai_support import build_model_request_headers
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.workspace import WorkspaceManager

if TYPE_CHECKING:
    from relay_teams.agents.execution.message_repository import MessageRepository
    from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
    from relay_teams.agents.orchestration.task_execution_service import (
        TaskExecutionService,
    )
    from relay_teams.agents.orchestration.task_orchestration_service import (
        TaskOrchestrationService,
    )
    from relay_teams.agents.tasks.task_repository import TaskRepository
    from relay_teams.metrics import MetricRecorder
    from relay_teams.notifications import NotificationService
    from relay_teams.persistence.shared_state_repo import SharedStateRepository
    from relay_teams.roles.memory_service import RoleMemoryService
    from relay_teams.roles.role_registry import RoleRegistry
    from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
    from relay_teams.sessions.runs.event_log import EventLog
    from relay_teams.sessions.runs.injection_queue import RunInjectionManager
    from relay_teams.sessions.runs.run_control_manager import RunControlManager
    from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
    from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
    from relay_teams.sessions.runs.user_question_manager import UserQuestionManager
    from relay_teams.sessions.runs.user_question_repository import (
        UserQuestionRepository,
    )
    from relay_teams.sessions.runs.todo_service import TodoService
    from relay_teams.skills.skill_registry import SkillRegistry
    from relay_teams.gateway.im import ImToolService
    from relay_teams.tools.registry import ToolRegistry
    from relay_teams.tools.runtime import ToolApprovalManager, ToolApprovalPolicy
    from relay_teams.tools.runtime.approval_ticket_repo import (
        ApprovalTicketRepository,
    )
    from relay_teams.tools.workspace_tools.shell_approval_repo import (
        ShellApprovalRepository,
    )

LOGGER = get_logger(__name__)
_EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS = 60.0
_EXTERNAL_ACP_PROMPT_TRAILING_ACTIVITY_GRACE_SECONDS = 0.2
_EXTERNAL_ACP_PROMPT_TRAILING_ACTIVITY_MAX_WAIT_SECONDS = 1.0
_EXTERNAL_ACP_EMPTY_RESPONSE_REPROMPT = (
    "Your previous reply was empty. Answer the user's last request with a non-empty "
    "assistant message now. If you need to return an image, reply with a single "
    "data:image/...;base64,... URL. Do not return an empty response."
)
_OPENCODE_CUSTOM_PROVIDER_ID = "agent_teams"
_OPENCODE_CUSTOM_API_KEY_ENV = "AGENT_TEAMS_OPENCODE_API_KEY"
_OPENCODE_ZAI_PROVIDER_ID = "zai"
_OPENCODE_ZAI_API_KEY_ENV = "ZHIPU_API_KEY"
_OPENCODE_ZAI_DEFAULT_CONTEXT_WINDOW = 128000


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
        user_question_repo: UserQuestionRepository | None,
        run_runtime_repo: RunRuntimeRepository,
        run_intent_repo: RunIntentRepository,
        background_task_service: BackgroundTaskService | None,
        todo_service: TodoService | None = None,
        monitor_service: MonitorService | None = None,
        role_memory_service: RoleMemoryService | None,
        tool_registry: ToolRegistry,
        get_mcp_registry: Callable[[], McpRegistry],
        get_skill_registry: Callable[[], SkillRegistry],
        get_role_registry: Callable[[], RoleRegistry],
        get_task_execution_service: Callable[[], TaskExecutionService],
        get_task_service: Callable[[], TaskOrchestrationService],
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        user_question_manager: UserQuestionManager | None,
        tool_approval_policy: ToolApprovalPolicy,
        get_notification_service: Callable[[], NotificationService | None],
        shell_approval_repo: ShellApprovalRepository | None = None,
        resolve_model_config: (
            Callable[[RoleDefinition, LLMRequest], ModelEndpointConfig | None] | None
        ) = None,
        media_asset_service: MediaAssetService | None = None,
        metric_recorder: MetricRecorder | None = None,
        im_tool_service: ImToolService | None = None,
        computer_runtime: ComputerRuntime | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._config_service = config_service
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._run_event_hub = run_event_hub
        self._workspace_manager = workspace_manager
        self._media_asset_service = media_asset_service
        self._task_repo = task_repo
        self._shared_store = shared_store
        self._event_bus = event_bus
        self._injection_manager = injection_manager
        self._agent_repo = agent_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._user_question_repo = user_question_repo
        self._run_runtime_repo = run_runtime_repo
        self._run_intent_repo = run_intent_repo
        self._background_task_service = background_task_service
        self._todo_service = todo_service
        self._monitor_service = monitor_service
        self._role_memory_service = role_memory_service
        self._tool_registry = tool_registry
        self._get_mcp_registry = get_mcp_registry
        self._get_skill_registry = get_skill_registry
        self._get_role_registry = get_role_registry
        self._get_task_execution_service = get_task_execution_service
        self._get_task_service = get_task_service
        self._run_control_manager = run_control_manager
        self._tool_approval_manager = tool_approval_manager
        self._user_question_manager = user_question_manager
        self._tool_approval_policy = tool_approval_policy
        self._shell_approval_repo = shell_approval_repo
        self._get_notification_service = get_notification_service
        self._resolve_model_config = resolve_model_config
        self._metric_recorder = metric_recorder
        self._im_tool_service = im_tool_service
        self._computer_runtime = computer_runtime
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
            output = ""
            try:
                output = await self._prompt_until_output(
                    agent_id=agent_id,
                    handle=handle,
                    request=request,
                    state=state,
                    prompt_text=prompt_text,
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
            if output:
                self._append_assistant_message(
                    request=request,
                    content=output,
                )
            return output

    async def _prompt_until_output(
        self,
        *,
        agent_id: str,
        handle: _ConversationHandle,
        request: LLMRequest,
        state: _ActivePromptState,
        prompt_text: str,
    ) -> str:
        include_host_tool_guidance = handle.host_tool_bridge.has_tools()
        retry_prompt = _compose_external_prompt(
            system_prompt=request.system_prompt,
            user_prompt="\n\n".join(
                part
                for part in (prompt_text, _EXTERNAL_ACP_EMPTY_RESPONSE_REPROMPT)
                if part.strip()
            ),
            include_host_tool_guidance=include_host_tool_guidance,
        )
        prompts = [
            _compose_external_prompt(
                system_prompt=request.system_prompt,
                user_prompt=prompt_text,
                include_host_tool_guidance=include_host_tool_guidance,
            ),
            retry_prompt,
        ]
        for attempt_index, attempt_prompt in enumerate(prompts):
            state.reset_activity()
            request_task = asyncio.create_task(
                handle.transport.send_request(
                    "session/prompt",
                    {
                        "sessionId": handle.external_session_id,
                        "messageId": _prompt_message_id(
                            request=request,
                            attempt_index=attempt_index,
                        ),
                        "prompt": [{"type": "text", "text": attempt_prompt}],
                    },
                )
            )
            await self._await_prompt_response(
                agent_id=agent_id,
                handle=handle,
                state=state,
                request_task=request_task,
            )
            output = state.resolve_output()
            if output:
                return output
            if attempt_index == 0:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="external_agent.prompt.empty_response_retry",
                    message=(
                        "External ACP prompt returned an empty response; retrying "
                        "with a non-empty answer instruction"
                    ),
                    payload={
                        "agent_id": agent_id,
                        "run_id": request.run_id,
                        "session_id": request.session_id,
                    },
                )
                continue
            raise RuntimeError("External ACP prompt returned an empty response")
        return ""

    async def _await_prompt_response(
        self,
        *,
        agent_id: str,
        handle: _ConversationHandle,
        state: _ActivePromptState,
        request_task: asyncio.Task[dict[str, JsonValue]],
    ) -> None:
        timeout_seconds = self._prompt_inactivity_timeout_seconds()
        while True:
            activity_task = asyncio.create_task(state.wait_for_activity())
            done, pending = await asyncio.wait(
                {request_task, activity_task},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if activity_task in pending:
                activity_task.cancel()
            if request_task in done:
                _ = await request_task
                await self._drain_trailing_prompt_activity(state)
                return
            if activity_task in done:
                with suppress(asyncio.CancelledError):
                    await activity_task
                continue
            await self._handle_prompt_timeout(
                agent_id=agent_id,
                handle=handle,
                state=state,
                request_task=request_task,
                timeout_seconds=timeout_seconds,
            )
            return

    async def _drain_trailing_prompt_activity(
        self,
        state: _ActivePromptState,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _EXTERNAL_ACP_PROMPT_TRAILING_ACTIVITY_MAX_WAIT_SECONDS
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(
                    state.wait_for_activity(),
                    timeout=min(
                        _EXTERNAL_ACP_PROMPT_TRAILING_ACTIVITY_GRACE_SECONDS,
                        remaining,
                    ),
                )
            except asyncio.TimeoutError:
                return

    async def _handle_prompt_timeout(
        self,
        *,
        agent_id: str,
        handle: _ConversationHandle,
        state: _ActivePromptState,
        request_task: asyncio.Task[dict[str, JsonValue]],
        timeout_seconds: float,
    ) -> None:
        with suppress(Exception):
            await self._cancel_prompt(handle)
        request_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await request_task
        fallback_output = state.timeout_fallback_output()
        if fallback_output is not None:
            log_event(
                LOGGER,
                logging.WARNING,
                event="external_agent.prompt.timeout_fallback",
                message=(
                    "External ACP prompt timed out after inactivity; using tool "
                    "result fallback"
                ),
                payload={
                    "agent_id": agent_id,
                    "run_id": state.request.run_id,
                    "session_id": state.request.session_id,
                },
            )
            state.text_chunks.append(fallback_output)
            self._publish_text_delta(request=state.request, text=fallback_output)
            return
        log_event(
            LOGGER,
            logging.ERROR,
            event="external_agent.prompt.timeout",
            message="External ACP prompt timed out after inactivity",
            payload={
                "agent_id": agent_id,
                "run_id": state.request.run_id,
                "session_id": state.request.session_id,
                "timeout_seconds": timeout_seconds,
            },
        )
        raise RuntimeError(
            "External ACP prompt timed out after "
            f"{timeout_seconds:g} seconds without updates"
        )

    def _prompt_inactivity_timeout_seconds(self) -> float:
        return max(
            _EXTERNAL_ACP_PROMPT_INACTIVITY_TIMEOUT_SECONDS,
            self._tool_approval_policy.timeout_seconds,
        )

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
        runtime_agent = self._resolve_transport_agent_config(
            agent=agent,
            role=role,
            request=request,
        )
        transport_signature = _transport_signature(runtime_agent)
        existing = self._conversations.get(key)
        if (
            existing is not None
            and existing.transport_signature
            and existing.transport_signature != transport_signature
        ):
            await existing.close()
            self._conversations.pop(key, None)
            existing = None
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
            config=runtime_agent,
            on_message=on_message,
            runtime_cwd=str(workspace.resolve_workdir()),
        )
        await transport.start()
        _ = await transport.send_request("initialize", {"protocolVersion": 1})
        handle = _ConversationHandle(
            transport=transport,
            external_session_id="",
            host_tool_bridge=self._create_host_tool_bridge(),
            transport_signature=transport_signature,
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
            agent=runtime_agent,
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
        handle.active_prompt.mark_activity()
        if update_name == "agent_message_chunk":
            text = _extract_content_text(update.get("content"))
            if not text:
                return
            handle.active_prompt.text_chunks.append(text)
            self._publish_text_delta(request=request, text=text)
            return
        if update_name == "agent_thought_chunk":
            text = _extract_content_text(update.get("content"))
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
            tool_title = _optional_str(update.get("title")) or "tool"
            tool_result = _annotate_external_computer_tool_result(
                tool_name=tool_title,
                tool_result=tool_result,
            )
            handle.active_prompt.note_tool_result(tool_result)
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
                            "tool_name": tool_title,
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
            user_question_repo=self._user_question_repo,
            run_runtime_repo=self._run_runtime_repo,
            run_intent_repo=self._run_intent_repo,
            background_task_service=self._background_task_service,
            todo_service=self._todo_service,
            monitor_service=self._monitor_service,
            workspace_manager=self._workspace_manager,
            media_asset_service=self._media_asset_service,
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
            user_question_manager=self._user_question_manager,
            tool_approval_policy=self._tool_approval_policy,
            shell_approval_repo=self._shell_approval_repo,
            get_notification_service=self._get_notification_service,
            metric_recorder=self._metric_recorder,
            im_tool_service=self._im_tool_service,
            computer_runtime=self._computer_runtime,
        )

    def _resolve_transport_agent_config(
        self,
        *,
        agent: ExternalAgentConfig,
        role: RoleDefinition,
        request: LLMRequest,
    ) -> ExternalAgentConfig:
        if not _is_opencode_agent(agent):
            return agent
        if self._resolve_model_config is None:
            return agent
        model_config = self._resolve_model_config(role, request)
        if model_config is None:
            return agent
        if model_config.provider == ProviderType.MAAS:
            raise RuntimeError(
                "MAAS model profiles are not supported for external ACP agents. Use the local runtime provider path instead."
            )
        return _apply_opencode_model_config(
            agent=agent,
            model_config=model_config,
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
        transport_signature: str = "",
    ) -> None:
        self.transport = transport
        self.external_session_id = external_session_id
        self.host_tool_bridge = host_tool_bridge
        self.transport_signature = transport_signature
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
        self.last_tool_result_text: str | None = None
        self._activity_event = asyncio.Event()

    def mark_activity(self) -> None:
        self._activity_event.set()

    async def wait_for_activity(self) -> None:
        await self._activity_event.wait()
        self._activity_event.clear()

    def reset_activity(self) -> None:
        self._activity_event.clear()

    def note_tool_result(self, tool_result: JsonValue) -> None:
        text = _extract_tool_result_text(tool_result)
        if text is not None:
            self.last_tool_result_text = text

    def resolve_output(self) -> str:
        text_output = "".join(self.text_chunks).strip()
        if text_output:
            return text_output
        fallback_output = self.timeout_fallback_output()
        if fallback_output is not None:
            return fallback_output
        return ""

    def timeout_fallback_output(self) -> str | None:
        if self.text_chunks:
            return None
        return _extract_image_timeout_fallback(self.last_tool_result_text)


def _conversation_key(*, session_id: str, role_id: str, agent_id: str) -> str:
    return f"{session_id}:{role_id}:{agent_id}"


def _is_opencode_agent(agent: ExternalAgentConfig) -> bool:
    transport = agent.transport
    if not isinstance(transport, StdioTransportConfig):
        return False
    tokens = [transport.command, *transport.args]
    return any(_is_opencode_command_token(token) for token in tokens)


def _is_opencode_command_token(token: str) -> bool:
    normalized = Path(token).name.lower()
    return (
        normalized == "opencode"
        or normalized.startswith("opencode@")
        or normalized == "opencode-ai"
        or normalized.startswith("opencode-ai@")
    )


def _apply_opencode_model_config(
    *,
    agent: ExternalAgentConfig,
    model_config: ModelEndpointConfig,
) -> ExternalAgentConfig:
    transport = agent.transport
    if not isinstance(transport, StdioTransportConfig):
        return agent
    runtime_config, runtime_env = _build_opencode_runtime_config(model_config)
    env = _upsert_env_binding(
        transport.env,
        name="OPENCODE_CONFIG_CONTENT",
        value=runtime_config,
    )
    for name, value in runtime_env:
        env = _upsert_env_binding(
            env,
            name=name,
            value=value,
        )
    if model_config.ssl_verify is False:
        env = _upsert_env_binding(
            env,
            name="NODE_TLS_REJECT_UNAUTHORIZED",
            value="0",
        )
    next_transport = transport.model_copy(
        update={
            "args": (
                transport.args
                if _is_opencode_acp_invocation(transport.args)
                else _inject_opencode_model_args(
                    transport.args,
                    _opencode_runtime_model_name(model_config),
                )
            ),
            "env": env,
        }
    )
    return agent.model_copy(update={"transport": next_transport})


def _opencode_runtime_model_name(model_config: ModelEndpointConfig) -> str:
    return f"{_opencode_runtime_provider_id(model_config)}/{model_config.model}"


def _opencode_runtime_provider_id(model_config: ModelEndpointConfig) -> str:
    if _should_use_opencode_zai_provider(model_config):
        return _OPENCODE_ZAI_PROVIDER_ID
    return _OPENCODE_CUSTOM_PROVIDER_ID


def _inject_opencode_model_args(
    args: tuple[str, ...],
    runtime_model: str,
) -> tuple[str, ...]:
    filtered_args: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in {"-m", "--model"}:
            skip_next = True
            continue
        if arg.startswith("--model="):
            continue
        filtered_args.append(arg)
    return ("--model", runtime_model, *filtered_args)


def _is_opencode_acp_invocation(args: tuple[str, ...]) -> bool:
    return any(arg == "acp" for arg in args)


def _upsert_env_binding(
    bindings: tuple[ExternalAgentSecretBinding, ...],
    *,
    name: str,
    value: str,
) -> tuple[ExternalAgentSecretBinding, ...]:
    normalized_name = name.strip()
    next_bindings: list[ExternalAgentSecretBinding] = []
    replaced = False
    for binding in bindings:
        if binding.name == normalized_name:
            next_bindings.append(
                binding.model_copy(
                    update={
                        "value": value,
                        "secret": True,
                        "configured": True,
                    }
                )
            )
            replaced = True
            continue
        next_bindings.append(binding)
    if not replaced:
        next_bindings.append(
            ExternalAgentSecretBinding(
                name=normalized_name,
                value=value,
                secret=True,
                configured=True,
            )
        )
    return tuple(next_bindings)


def _build_opencode_runtime_config(
    model_config: ModelEndpointConfig,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    custom_headers = _opencode_custom_headers(model_config)
    should_emit_api_key = _opencode_should_emit_api_key(model_config)
    if _should_use_opencode_zai_provider(model_config):
        return (
            _build_opencode_zai_config_content(
                model_config,
                custom_headers=custom_headers,
                include_api_key=should_emit_api_key,
            ),
            (
                ((_OPENCODE_ZAI_API_KEY_ENV, model_config.api_key),)
                if should_emit_api_key and model_config.api_key is not None
                else ()
            ),
        )
    return (
        _build_opencode_custom_config_content(
            model_config,
            custom_headers=custom_headers,
            include_api_key=should_emit_api_key,
        ),
        (
            ((_OPENCODE_CUSTOM_API_KEY_ENV, model_config.api_key),)
            if should_emit_api_key and model_config.api_key is not None
            else ()
        ),
    )


def _should_use_opencode_zai_provider(model_config: ModelEndpointConfig) -> bool:
    if model_config.provider == ProviderType.BIGMODEL:
        return True
    normalized_base_url = model_config.base_url.strip().lower()
    return "bigmodel.cn" in normalized_base_url or "z.ai" in normalized_base_url


def _build_opencode_custom_config_content(
    model_config: ModelEndpointConfig,
    *,
    custom_headers: dict[str, str],
    include_api_key: bool,
) -> str:
    model_entry = _build_opencode_model_entry(model_config)
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "model": _opencode_runtime_model_name(model_config),
        "provider": {
            _OPENCODE_CUSTOM_PROVIDER_ID: {
                "api": model_config.base_url,
                **(
                    {"env": [_OPENCODE_CUSTOM_API_KEY_ENV]}
                    if include_api_key and model_config.api_key is not None
                    else {}
                ),
                **({"options": {"headers": custom_headers}} if custom_headers else {}),
                "npm": "@ai-sdk/openai-compatible",
                "models": {
                    model_config.model: model_entry,
                },
            }
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_opencode_zai_config_content(
    model_config: ModelEndpointConfig,
    *,
    custom_headers: dict[str, str],
    include_api_key: bool,
) -> str:
    model_entry = _build_opencode_zai_model_entry(model_config)
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "model": _opencode_runtime_model_name(model_config),
        "provider": {
            _OPENCODE_ZAI_PROVIDER_ID: {
                "api": model_config.base_url,
                **(
                    {"env": [_OPENCODE_ZAI_API_KEY_ENV]}
                    if include_api_key and model_config.api_key is not None
                    else {}
                ),
                **({"options": {"headers": custom_headers}} if custom_headers else {}),
                "npm": "@ai-sdk/openai-compatible",
                "models": {
                    model_config.model: model_entry,
                },
            }
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_opencode_model_entry(model_config: ModelEndpointConfig) -> dict[str, object]:
    model_entry: dict[str, object] = {
        "name": model_config.model,
    }
    limit = _build_opencode_limit(model_config)
    if limit is not None:
        model_entry["limit"] = limit
    return model_entry


def _build_opencode_zai_model_entry(
    model_config: ModelEndpointConfig,
) -> dict[str, object]:
    model_entry = _build_opencode_model_entry(model_config)
    supports_attachments = _opencode_model_supports_attachments(model_config.model)
    input_modalities = ["text"]
    if supports_attachments:
        input_modalities.extend(["image", "video"])
    model_entry.update(
        {
            "attachment": supports_attachments,
            "tool_call": not _opencode_model_disables_tool_calls(model_config.model),
            "modalities": {
                "input": input_modalities,
                "output": ["text"],
            },
        }
    )
    if "limit" not in model_entry:
        model_entry["limit"] = _build_opencode_limit(
            model_config,
            fallback_context_window=_OPENCODE_ZAI_DEFAULT_CONTEXT_WINDOW,
        )
    return model_entry


def _build_opencode_limit(
    model_config: ModelEndpointConfig,
    *,
    fallback_context_window: int | None = None,
) -> dict[str, int] | None:
    context_window = model_config.context_window
    max_tokens = model_config.sampling.max_tokens
    if context_window is None:
        context_window = fallback_context_window
    if context_window is None or max_tokens is None or max_tokens <= 0:
        return None
    return {
        "context": context_window,
        "output": max_tokens,
    }


def _opencode_custom_headers(model_config: ModelEndpointConfig) -> dict[str, str]:
    headers = build_model_request_headers(model_config)
    if model_config.api_key is not None and "Authorization" in headers:
        authorization_override = next(
            (
                header.value
                for header in model_config.headers
                if header.value is not None
                and header.name.casefold() == "authorization"
            ),
            None,
        )
        if authorization_override is None:
            headers.pop("Authorization", None)
    return headers


def _opencode_should_emit_api_key(model_config: ModelEndpointConfig) -> bool:
    if model_config.api_key is None:
        return False
    return not any(
        header.value is not None and header.name.casefold() == "authorization"
        for header in model_config.headers
    )


def _opencode_model_supports_attachments(model_name: str) -> bool:
    return (
        re.search(
            r"(?:^|[-.])[0-9]+(?:\.[0-9]+)?v(?:[-.]|$)",
            model_name.strip().lower(),
        )
        is not None
    )


def _opencode_model_disables_tool_calls(model_name: str) -> bool:
    normalized = model_name.strip().lower()
    return _opencode_model_supports_attachments(normalized) and "flash" in normalized


def _build_opencode_config_content(model_config: ModelEndpointConfig) -> str:
    payload, _ = _build_opencode_runtime_config(model_config)
    return payload


def _transport_signature(agent: ExternalAgentConfig) -> str:
    return json.dumps(
        agent.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _build_mcp_servers_for_role(
    *,
    role: RoleDefinition,
    mcp_registry: McpRegistry,
    host_server: dict[str, JsonValue] | None = None,
) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    resolved_server_names = mcp_registry.resolve_server_names(
        role.mcp_servers,
        strict=False,
        consumer=f"external_agents.provider.role:{role.role_id}",
    )
    for server_name in resolved_server_names:
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
    raw_content = first_item.get("content")
    inner_content = _as_object(raw_content)
    text = _extract_content_text(raw_content)
    if not text:
        return {}
    if _optional_str(inner_content.get("type")) != "text":
        return {"text": text}
    try:
        return cast(JsonValue, json.loads(text))
    except json.JSONDecodeError:
        return {"text": text}


def _annotate_external_computer_tool_result(
    *,
    tool_name: str,
    tool_result: JsonValue,
) -> JsonValue:
    descriptor = describe_external_acp_tool(tool_name)
    if descriptor is None:
        return tool_result
    if isinstance(tool_result, dict):
        result_map = cast(dict[str, JsonValue], tool_result)
        if isinstance(result_map.get("computer"), dict):
            return tool_result
        content = result_map.get("content")
        content_blocks: tuple[dict[str, JsonValue], ...] = ()
        if isinstance(content, list):
            content_blocks = tuple(
                {
                    str(key): cast(JsonValue, value)
                    for key, value in item.items()
                    if isinstance(key, str)
                }
                for item in content
                if isinstance(item, dict)
            )
        observation = result_map.get("observation")
        observation_map = (
            {
                str(key): cast(JsonValue, value)
                for key, value in observation.items()
                if isinstance(key, str)
            }
            if isinstance(observation, dict)
            else None
        )
        payload = build_computer_tool_payload(
            descriptor=descriptor,
            text=_extract_tool_result_text(tool_result)
            or json.dumps(tool_result, ensure_ascii=False, default=str),
            content=content_blocks,
            observation=observation_map,
            data={
                key: value
                for key, value in result_map.items()
                if key not in {"text", "content", "computer", "observation"}
            }
            or None,
        )
        return cast(JsonValue, payload)
    return cast(
        JsonValue,
        build_computer_tool_payload(
            descriptor=descriptor,
            text=json.dumps(tool_result, ensure_ascii=False, default=str),
        ),
    )


def _extract_tool_result_text(tool_result: JsonValue) -> str | None:
    if not isinstance(tool_result, dict):
        return None
    return _optional_str(tool_result.get("text"))


def _extract_image_timeout_fallback(text: str | None) -> str | None:
    normalized = _optional_str(text)
    if normalized is None:
        return None
    if normalized.startswith("data:image/") and ";base64," in normalized:
        return normalized
    compact = "".join(normalized.split())
    if len(compact) < 64:
        return None
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None
    mime_type = _detect_supported_image_mime_type(decoded)
    if mime_type is not None:
        return f"data:{mime_type};base64,{compact}"
    return None


def _detect_supported_image_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _prompt_message_id(*, request: LLMRequest, attempt_index: int) -> str:
    suffix = "" if attempt_index == 0 else f":retry-{attempt_index}"
    return f"{request.run_id}:{request.task_id}{suffix}"


def _extract_content_text(content: JsonValue | None) -> str | None:
    content_object = _as_object(content)
    content_type = _optional_str(content_object.get("type"))
    if content_type == "text":
        return _optional_str(content_object.get("text"))
    if content_type in {"image", "audio"}:
        return _extract_binary_content_text(content_object)
    if content_type == "resource_link":
        return _optional_str(content_object.get("uri"))
    if content_type == "resource":
        resource = _as_object(content_object.get("resource"))
        text = _optional_str(resource.get("text"))
        if text is not None:
            return text
        return _extract_binary_resource_text(resource)
    return None


def _extract_binary_content_text(content: dict[str, JsonValue]) -> str | None:
    data = _optional_str(content.get("data"))
    mime_type = _optional_str(content.get("mimeType"))
    if data is not None:
        if mime_type is not None:
            return f"data:{mime_type};base64,{data}"
        return data
    return _optional_str(content.get("uri"))


def _extract_binary_resource_text(resource: dict[str, JsonValue]) -> str | None:
    blob = _optional_str(resource.get("blob"))
    mime_type = _optional_str(resource.get("mimeType"))
    if blob is not None:
        if mime_type is not None:
            return f"data:{mime_type};base64,{blob}"
        return blob
    return _optional_str(resource.get("uri"))


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
