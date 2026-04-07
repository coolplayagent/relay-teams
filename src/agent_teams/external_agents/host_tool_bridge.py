# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

import mcp.types as mcp_types
from fastmcp.server.server import FastMCP
from fastmcp.tools.base import Tool as FastMcpTool
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from pydantic import JsonValue, PrivateAttr
from pydantic_ai import RunContext
from pydantic_ai.models import Model
from pydantic_ai.tools import Tool as PydanticTool
from pydantic_ai.usage import RunUsage

from agent_teams.agents.execution.coordination_agent_builder import (
    build_coordination_agent,
)
from agent_teams.computer import ComputerRuntime
from agent_teams.media import MediaAssetService
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.providers.provider_contracts import LLMRequest
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.background_tasks import BackgroundTaskService
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry, ToolResolutionContext
from agent_teams.tools.runtime import (
    ToolApprovalManager,
    ToolApprovalPolicy,
    ToolDeps,
)
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
)
from agent_teams.workspace import WorkspaceManager, build_conversation_id

if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

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
    from agent_teams.gateway.im import ImToolService


HOST_TOOL_SERVER_ID = "agent_teams_host_tools"
BUILTIN_TOOL_NAME_PREFIX = "agent_teams_builtin_"
SKILL_TOOL_NAME_PREFIX = "agent_teams_skill_"
HOST_TOOL_STDIO_MODULE = "agent_teams.external_agents.host_tool_stdio_server"
HOST_TOOL_CONFIG_DIR_ENV = "AGENT_TEAMS_CONFIG_DIR"
HOST_TOOL_RUN_ID_ENV = "AGENT_TEAMS_HOST_TOOL_RUN_ID"
HOST_TOOL_TRACE_ID_ENV = "AGENT_TEAMS_HOST_TOOL_TRACE_ID"
HOST_TOOL_TASK_ID_ENV = "AGENT_TEAMS_HOST_TOOL_TASK_ID"
HOST_TOOL_SESSION_ID_ENV = "AGENT_TEAMS_HOST_TOOL_SESSION_ID"
HOST_TOOL_WORKSPACE_ID_ENV = "AGENT_TEAMS_HOST_TOOL_WORKSPACE_ID"
HOST_TOOL_CONVERSATION_ID_ENV = "AGENT_TEAMS_HOST_TOOL_CONVERSATION_ID"
HOST_TOOL_INSTANCE_ID_ENV = "AGENT_TEAMS_HOST_TOOL_INSTANCE_ID"
HOST_TOOL_ROLE_ID_ENV = "AGENT_TEAMS_HOST_TOOL_ROLE_ID"


class HostedToolDefinition:
    def __init__(
        self,
        *,
        source: Literal["builtin", "skill"],
        exposed_name: str,
        raw_name: str,
        description: str,
        input_schema: dict[str, JsonValue],
        tool: PydanticTool[ToolDeps],
    ) -> None:
        self.source = source
        self.exposed_name = exposed_name
        self.raw_name = raw_name
        self.description = description
        self.input_schema = input_schema
        self.tool = tool


class ExternalAcpHostToolBridge:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        shared_store: SharedStateRepository,
        event_bus: EventLog,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        agent_repo: AgentInstanceRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        run_runtime_repo: RunRuntimeRepository,
        run_intent_repo: RunIntentRepository,
        background_task_service: BackgroundTaskService | None,
        workspace_manager: WorkspaceManager,
        role_memory_service: RoleMemoryService | None,
        tool_registry: ToolRegistry,
        message_repo: MessageRepository,
        get_mcp_registry: Callable[[], McpRegistry],
        get_skill_registry: Callable[[], SkillRegistry],
        get_role_registry: Callable[[], RoleRegistry],
        get_task_execution_service: Callable[[], TaskExecutionService],
        get_task_service: Callable[[], TaskOrchestrationService],
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        tool_approval_policy: ToolApprovalPolicy,
        get_notification_service: Callable[[], NotificationService | None],
        media_asset_service: MediaAssetService | None = None,
        metric_recorder: MetricRecorder | None = None,
        im_tool_service: ImToolService | None = None,
        computer_runtime: ComputerRuntime | None = None,
        shell_approval_repo: ShellApprovalRepository | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._shared_store = shared_store
        self._event_bus = event_bus
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._agent_repo = agent_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._run_runtime_repo = run_runtime_repo
        self._run_intent_repo = run_intent_repo
        self._background_task_service = background_task_service
        self._workspace_manager = workspace_manager
        self._media_asset_service = media_asset_service
        self._role_memory_service = role_memory_service
        self._tool_registry = tool_registry
        self._message_repo = message_repo
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
        self._im_tool_service = im_tool_service
        self._computer_runtime = computer_runtime
        self._shell_approval_repo = shell_approval_repo

        self._catalog_by_name: dict[str, HostedToolDefinition] = {}
        self._catalog_signature = ""
        self._server: FastMCP | None = None
        self._connections: dict[str, _HostedMcpConnection] = {}
        self._active_request: LLMRequest | None = None
        self._role: RoleDefinition | None = None
        self._session_id = ""
        self._external_session_id = ""
        self._send_request: (
            Callable[[str, dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]
            | None
        ) = None
        self._send_notification: (
            Callable[[str, dict[str, JsonValue]], Awaitable[None]] | None
        ) = None
        self._context_model = build_coordination_agent(
            model_name="host-tools-model",
            base_url="https://example.invalid/v1",
            api_key="host-tools",
            system_prompt="host-tools-model",
            allowed_tools=(),
            allowed_mcp_servers=(),
            allowed_skills=(),
            tool_registry=self._tool_registry,
            mcp_registry=None,
            skill_registry=None,
        ).model

    async def configure(
        self,
        *,
        role: RoleDefinition,
        session_id: str,
        external_session_id: str,
        send_request: Callable[
            [str, dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]
        ],
        send_notification: Callable[[str, dict[str, JsonValue]], Awaitable[None]],
    ) -> bool:
        catalog = self._build_catalog(role=role, session_id=session_id)
        signature = _catalog_signature(catalog)
        changed = signature != self._catalog_signature
        self._role = role
        self._session_id = session_id
        self._external_session_id = external_session_id
        self._send_request = send_request
        self._send_notification = send_notification
        if not changed:
            return False
        await self.close_connections()
        self._catalog_by_name = {item.exposed_name: item for item in catalog}
        self._catalog_signature = signature
        self._server = self._build_server()
        return True

    def has_tools(self) -> bool:
        return bool(self._catalog_by_name)

    def server_payload(self) -> dict[str, JsonValue] | None:
        if not self.has_tools():
            return None
        return {
            "id": HOST_TOOL_SERVER_ID,
            "name": HOST_TOOL_SERVER_ID,
            "transport": "acp",
            "acpId": HOST_TOOL_SERVER_ID,
        }

    def stdio_server_payload(
        self,
        *,
        config_dir: Path,
        request: LLMRequest,
    ) -> dict[str, JsonValue] | None:
        if not self.has_tools():
            return None
        return build_host_tool_stdio_server_payload(
            config_dir=config_dir,
            request=request,
        )

    def bind_active_request(self, request: LLMRequest) -> None:
        self._active_request = request

    def clear_active_request(self) -> None:
        self._active_request = None

    def require_server(self) -> FastMCP:
        if self._server is None:
            raise RuntimeError("Host tool bridge server is not configured.")
        return self._server

    async def open_connection(self, *, server_id: str) -> dict[str, JsonValue]:
        if server_id != HOST_TOOL_SERVER_ID or not self.has_tools():
            raise KeyError(f"Unknown host MCP server_id: {server_id}")
        connection_id = f"conn_{uuid4().hex[:12]}"
        connection = _HostedMcpConnection(
            connection_id=connection_id,
            external_session_id=self._external_session_id,
            server=cast(FastMCP, self._server),
            send_request=cast(
                Callable[[str, dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]],
                self._send_request,
            ),
            send_notification=cast(
                Callable[[str, dict[str, JsonValue]], Awaitable[None]],
                self._send_notification,
            ),
        )
        await connection.start()
        self._connections[connection_id] = connection
        return {
            "connectionId": connection_id,
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
        connection = self._connections.get(connection_id)
        if connection is None:
            raise KeyError(f"Unknown host MCP connection_id: {connection_id}")
        return await connection.handle_message(
            method=method,
            params=params,
            message_id=message_id,
        )

    async def close_connection(self, *, connection_id: str) -> dict[str, JsonValue]:
        connection = self._connections.pop(connection_id, None)
        if connection is None:
            raise KeyError(f"Unknown host MCP connection_id: {connection_id}")
        await connection.close()
        return {"status": "closed", "connectionId": connection_id}

    async def close_connections(self) -> None:
        connections = list(self._connections.values())
        self._connections.clear()
        for connection in connections:
            await connection.close()

    async def close(self) -> None:
        self.clear_active_request()
        await self.close_connections()

    def _build_catalog(
        self,
        *,
        role: RoleDefinition,
        session_id: str,
    ) -> tuple[HostedToolDefinition, ...]:
        skill_registry = self._get_skill_registry()
        resolved_skills = skill_registry.resolve_known(
            role.skills,
            strict=False,
            consumer="external_agents.host_tool_bridge.build_catalog",
        )
        allowed_tools = self._tool_registry.resolve_names(
            role.tools,
            context=ToolResolutionContext(session_id=session_id),
        )
        skill_tool_names = frozenset(
            tool.name for tool in skill_registry.get_toolset_tools(resolved_skills)
        )
        tool_agent = build_coordination_agent(
            model_name="host-tools-model",
            base_url="https://example.invalid/v1",
            api_key="host-tools",
            system_prompt="host-tools-catalog",
            allowed_tools=allowed_tools,
            allowed_mcp_servers=(),
            allowed_skills=resolved_skills,
            tool_registry=self._tool_registry,
            mcp_registry=None,
            skill_registry=skill_registry,
        )
        catalog: list[HostedToolDefinition] = []
        for tool in tool_agent._function_toolset.tools.values():
            source: Literal["builtin", "skill"] = (
                "skill" if tool.name in skill_tool_names else "builtin"
            )
            catalog.append(
                HostedToolDefinition(
                    source=source,
                    exposed_name=_exposed_tool_name(
                        source=source, name=tool.tool_def.name
                    ),
                    raw_name=tool.tool_def.name,
                    description=tool.tool_def.description or "",
                    input_schema=(
                        dict(tool.tool_def.parameters_json_schema)
                        if isinstance(tool.tool_def.parameters_json_schema, dict)
                        else {}
                    ),
                    tool=tool,
                )
            )
        return tuple(sorted(catalog, key=lambda item: item.exposed_name))

    def _build_server(self) -> FastMCP:
        server = FastMCP(
            name=HOST_TOOL_SERVER_ID,
            instructions="Agent Teams host tools for the active ACP prompt.",
        )
        for definition in self._catalog_by_name.values():
            server.add_tool(
                _HostedFastMcpTool(
                    bridge=self,
                    definition=definition,
                )
            )
        return server

    async def _run_hosted_tool(
        self,
        *,
        definition: HostedToolDefinition,
        arguments: dict[str, object],
    ) -> object:
        request = self._active_request
        if request is None:
            raise McpError(
                mcp_types.ErrorData(
                    code=-32000,
                    message=(
                        "Agent Teams host tools are only available during an active "
                        "external ACP prompt."
                    ),
                )
            )
        if self._role is None:
            raise McpError(
                mcp_types.ErrorData(
                    code=-32000,
                    message="Host tool bridge is not configured with a role.",
                )
            )
        deps = self._build_tool_deps(request=request)
        tool = definition.tool
        tool_call_id = f"acp_mcp_{uuid4().hex[:12]}"
        ctx = RunContext[ToolDeps](
            deps=deps,
            model=cast(Model, self._context_model),
            usage=RunUsage(),
            tool_call_id=tool_call_id,
            tool_name=definition.raw_name,
            run_id=request.run_id,
        )
        validated_arguments = cast(
            dict[str, object],
            tool.function_schema.validator.validate_python(arguments),
        )
        if tool.args_validator is not None:
            validation_args, validation_kwargs = tool.function_schema._call_args(
                dict(validated_arguments),
                ctx,
            )
            validation_result = tool.args_validator(
                *validation_args,
                **validation_kwargs,
            )
            if inspect.isawaitable(validation_result):
                await validation_result
        return await tool.function_schema.call(
            dict(validated_arguments),
            ctx,
        )

    def _build_tool_deps(self, *, request: LLMRequest) -> ToolDeps:
        resolved_conversation_id = request.conversation_id or build_conversation_id(
            request.session_id,
            request.role_id,
        )
        yolo = False
        try:
            yolo = self._run_intent_repo.get(request.run_id).yolo
        except KeyError:
            yolo = False
        return ToolDeps(
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            event_bus=self._event_bus,
            message_repo=self._message_repo,
            approval_ticket_repo=self._approval_ticket_repo,
            run_runtime_repo=self._run_runtime_repo,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            agent_repo=self._agent_repo,
            workspace=self._workspace_manager.resolve(
                session_id=request.session_id,
                role_id=request.role_id,
                instance_id=request.instance_id,
                workspace_id=request.workspace_id,
                conversation_id=resolved_conversation_id,
            ),
            role_memory=self._role_memory_service,
            media_asset_service=self._media_asset_service,
            computer_runtime=self._computer_runtime,
            background_task_service=self._background_task_service,
            run_id=request.run_id,
            trace_id=request.trace_id,
            task_id=request.task_id,
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            conversation_id=resolved_conversation_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            role_registry=self._get_role_registry(),
            mcp_registry=self._get_mcp_registry(),
            task_service=self._get_task_service(),
            task_execution_service=self._get_task_execution_service(),
            run_control_manager=self._run_control_manager,
            tool_approval_manager=self._tool_approval_manager,
            tool_approval_policy=self._tool_approval_policy.with_yolo(yolo),
            shell_approval_repo=self._shell_approval_repo,
            metric_recorder=self._metric_recorder,
            notification_service=self._get_notification_service(),
            im_tool_service=self._im_tool_service,
        )


class _HostedFastMcpTool(FastMcpTool):
    _bridge: ExternalAcpHostToolBridge = PrivateAttr()
    _definition: HostedToolDefinition = PrivateAttr()

    def __init__(
        self,
        *,
        bridge: ExternalAcpHostToolBridge,
        definition: HostedToolDefinition,
    ) -> None:
        super().__init__(
            name=definition.exposed_name,
            description=definition.description,
            parameters=dict(definition.input_schema),
            output_schema=None,
        )
        object.__setattr__(self, "_bridge", bridge)
        object.__setattr__(self, "_definition", definition)

    async def run(self, arguments: dict[str, object]) -> ToolResult:
        result = await self._bridge._run_hosted_tool(
            definition=self._definition,
            arguments=arguments,
        )
        if isinstance(result, dict):
            return ToolResult(structured_content=result)
        return ToolResult(content=result)


class _HostedMcpConnection:
    def __init__(
        self,
        *,
        connection_id: str,
        external_session_id: str,
        server: FastMCP,
        send_request: Callable[
            [str, dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]
        ],
        send_notification: Callable[[str, dict[str, JsonValue]], Awaitable[None]],
    ) -> None:
        self._connection_id = connection_id
        self._external_session_id = external_session_id
        self._server = server
        self._send_request = send_request
        self._send_notification = send_notification
        self._write_stream: MemoryObjectSendStream[SessionMessage] | None = None
        self._pending: dict[str | int, asyncio.Future[dict[str, JsonValue]]] = {}
        self._message_id = 0
        self._exit_stack: contextlib.AsyncExitStack | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._pump_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._write_stream is not None:
            return
        exit_stack = contextlib.AsyncExitStack()
        client_streams, server_streams = await exit_stack.enter_async_context(
            create_client_server_memory_streams()
        )
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        self._write_stream = client_write
        self._exit_stack = exit_stack
        self._server_task = asyncio.create_task(
            self._run_server(
                server_read=server_read,
                server_write=server_write,
            )
        )
        self._pump_task = asyncio.create_task(
            self._pump_messages(
                read_stream=client_read,
                write_stream=client_write,
            )
        )

    async def handle_message(
        self,
        *,
        method: str,
        params: dict[str, JsonValue],
        message_id: str | int | None,
    ) -> dict[str, JsonValue]:
        await self.start()
        if self._write_stream is None:
            raise RuntimeError("Hosted MCP connection is not started")
        if message_id is None:
            notification = mcp_types.JSONRPCNotification(
                jsonrpc="2.0",
                method=method,
                params=params or None,
            )
            await self._write_stream.send(
                SessionMessage(message=mcp_types.JSONRPCMessage(notification))
            )
            return {}
        internal_id = self._next_message_id()
        future: asyncio.Future[dict[str, JsonValue]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[internal_id] = future
        request = mcp_types.JSONRPCRequest(
            jsonrpc="2.0",
            id=internal_id,
            method=method,
            params=params or None,
        )
        await self._write_stream.send(
            SessionMessage(message=mcp_types.JSONRPCMessage(request))
        )
        try:
            return await future
        finally:
            self._pending.pop(internal_id, None)

    async def close(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
        if self._server_task is not None:
            self._server_task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._write_stream = None
        self._exit_stack = None
        self._server_task = None
        self._pump_task = None

    async def _run_server(
        self,
        *,
        server_read: MemoryObjectReceiveStream[SessionMessage | Exception],
        server_write: MemoryObjectSendStream[SessionMessage],
    ) -> None:
        async with self._server._lifespan_manager():
            await self._server._mcp_server.run(
                server_read,
                server_write,
                self._server._mcp_server.create_initialization_options(),
                raise_exceptions=True,
            )

    async def _pump_messages(
        self,
        *,
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception],
        write_stream: MemoryObjectSendStream[SessionMessage],
    ) -> None:
        async with read_stream:
            async for item in read_stream:
                if isinstance(item, Exception):
                    for future in self._pending.values():
                        if not future.done():
                            future.set_exception(item)
                    continue
                raw_message = item.message.root
                if isinstance(raw_message, mcp_types.JSONRPCResponse):
                    future = self._pending.get(raw_message.id)
                    if future is not None and not future.done():
                        future.set_result(
                            dict(raw_message.result)
                            if isinstance(raw_message.result, dict)
                            else {}
                        )
                    continue
                if isinstance(raw_message, mcp_types.JSONRPCError):
                    future = self._pending.get(raw_message.id)
                    if future is not None and not future.done():
                        future.set_result(
                            {
                                "error": raw_message.error.model_dump(
                                    mode="json",
                                    by_alias=True,
                                )
                            }
                        )
                    continue
                if isinstance(raw_message, mcp_types.JSONRPCRequest):
                    response = await self._send_request(
                        "mcp/message",
                        _build_mcp_message_request(
                            session_id=self._external_session_id,
                            connection_id=self._connection_id,
                            method=raw_message.method,
                            params=_json_object(raw_message.params),
                        ),
                    )
                    await write_stream.send(
                        SessionMessage(
                            message=mcp_types.JSONRPCMessage(
                                _jsonrpc_message_from_acp_response(
                                    raw_request_id=raw_message.id,
                                    response=response,
                                )
                            )
                        )
                    )
                    continue
                if isinstance(raw_message, mcp_types.JSONRPCNotification):
                    await self._send_notification(
                        "mcp/message",
                        _build_mcp_message_request(
                            session_id=self._external_session_id,
                            connection_id=self._connection_id,
                            method=raw_message.method,
                            params=_json_object(raw_message.params),
                        ),
                    )

    def _next_message_id(self) -> int:
        self._message_id += 1
        return self._message_id


def _catalog_signature(catalog: tuple[HostedToolDefinition, ...]) -> str:
    serialized = tuple(
        {
            "source": item.source,
            "exposed_name": item.exposed_name,
            "raw_name": item.raw_name,
            "input_schema": item.input_schema,
        }
        for item in catalog
    )
    return json.dumps(serialized, ensure_ascii=False, sort_keys=True, default=str)


def build_host_tool_stdio_server_payload(
    *,
    config_dir: Path,
    request: LLMRequest,
    python_executable: str | None = None,
) -> dict[str, JsonValue]:
    executable = python_executable or sys.executable
    return {
        "name": HOST_TOOL_SERVER_ID,
        "command": executable,
        "args": ["-m", HOST_TOOL_STDIO_MODULE],
        "env": _build_stdio_env_vars(
            config_dir=config_dir,
            request=request,
        ),
    }


def _exposed_tool_name(*, source: Literal["builtin", "skill"], name: str) -> str:
    if source == "skill":
        return f"{SKILL_TOOL_NAME_PREFIX}{name}"
    return f"{BUILTIN_TOOL_NAME_PREFIX}{name}"


def _build_stdio_env_vars(
    *,
    config_dir: Path,
    request: LLMRequest,
) -> list[JsonValue]:
    return [
        _env_var(HOST_TOOL_CONFIG_DIR_ENV, str(config_dir)),
        _env_var(HOST_TOOL_RUN_ID_ENV, request.run_id),
        _env_var(HOST_TOOL_TRACE_ID_ENV, request.trace_id),
        _env_var(HOST_TOOL_TASK_ID_ENV, request.task_id),
        _env_var(HOST_TOOL_SESSION_ID_ENV, request.session_id),
        _env_var(HOST_TOOL_WORKSPACE_ID_ENV, request.workspace_id),
        _env_var(HOST_TOOL_CONVERSATION_ID_ENV, request.conversation_id),
        _env_var(HOST_TOOL_INSTANCE_ID_ENV, request.instance_id),
        _env_var(HOST_TOOL_ROLE_ID_ENV, request.role_id),
    ]


def _env_var(name: str, value: str) -> dict[str, JsonValue]:
    return {
        "name": name,
        "value": value,
    }


def _json_object(value: object) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return cast(dict[str, JsonValue], value)
    return {}


def _build_mcp_message_request(
    *,
    session_id: str,
    connection_id: str,
    method: str,
    params: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "sessionId": session_id,
        "connectionId": connection_id,
        "method": method,
    }
    if params:
        payload["params"] = params
    return payload


def _jsonrpc_message_from_acp_response(
    *,
    raw_request_id: str | int,
    response: dict[str, JsonValue],
) -> mcp_types.JSONRPCResponse | mcp_types.JSONRPCError:
    raw_error = response.get("error")
    if isinstance(raw_error, dict):
        return mcp_types.JSONRPCError(
            jsonrpc="2.0",
            id=raw_request_id,
            error=mcp_types.ErrorData.model_validate(raw_error),
        )
    raw_result = response.get("result")
    result_payload = raw_result if isinstance(raw_result, dict) else {}
    return mcp_types.JSONRPCResponse(
        jsonrpc="2.0",
        id=raw_request_id,
        result=result_payload,
    )
