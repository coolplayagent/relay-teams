# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, cast

import anyio
import mcp.types as mcp_types
from pydantic import JsonValue
from pydantic_ai.mcp import MCPServer

from agent_teams.gateway.gateway_models import GatewayMcpServerSpec
from agent_teams.mcp.mcp_models import (
    McpConfigScope,
    McpServerSpec,
    McpToolInfo,
    McpToolSchema,
)
from agent_teams.mcp.mcp_registry import (
    McpRegistry,
    build_mcp_server,
    get_effective_mcp_tool_name,
    get_mcp_tool_prefix,
)
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage

if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream


type JsonRpcId = str | int
type AcpRequestSender = Callable[
    [str, dict[str, JsonValue]],
    Awaitable[dict[str, JsonValue]],
]
type AcpNotificationSender = Callable[[dict[str, JsonValue]], Awaitable[None]]


_CURRENT_GATEWAY_SESSION_ID: ContextVar[str | None] = ContextVar(
    "gateway_session_mcp_session_id",
    default=None,
)


class GatewayAwareMcpRegistry(McpRegistry):
    def __init__(
        self,
        *,
        base_registry: McpRegistry,
        relay: AcpMcpRelay,
    ) -> None:
        super().__init__(())
        self._base_registry = base_registry
        self._relay = relay

    def validate_known(self, names: tuple[str, ...]) -> None:
        missing = [name for name in names if name not in self.list_names()]
        if missing:
            raise ValueError(f"Unknown MCP servers: {missing}")

    def resolve_server_names(
        self,
        names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        _ = consumer
        resolved: list[str] = []
        for name in names:
            if name not in resolved:
                resolved.append(name)
        for name in self._relay.current_session_server_names():
            if name not in resolved:
                resolved.append(name)
        if strict:
            self.validate_known(tuple(resolved))
        else:
            resolved = [name for name in resolved if name in self.list_names()]
        return tuple(resolved)

    def list_names(self) -> tuple[str, ...]:
        combined = set(self._base_registry.list_names()) | set(
            self._relay.current_session_server_names()
        )
        return tuple(sorted(combined))

    def list_specs(self) -> tuple[McpServerSpec, ...]:
        return self._base_registry.list_specs() + self._relay.current_session_specs()

    def get_spec(self, name: str) -> McpServerSpec:
        try:
            return self._base_registry.get_spec(name)
        except ValueError:
            return self._relay.current_session_spec(name)

    def get_toolsets(self, names: tuple[str, ...]) -> tuple[MCPServer, ...]:
        self.validate_known(names)
        toolsets: list[MCPServer] = []
        session_toolsets = self._relay.current_session_toolsets()
        base_names: list[str] = []
        for name in names:
            toolset = session_toolsets.get(name)
            if toolset is not None:
                toolsets.append(toolset)
                continue
            base_names.append(name)
        if base_names:
            toolsets.extend(self._base_registry.get_toolsets(tuple(base_names)))
        return tuple(toolsets)

    async def list_tools(self, name: str) -> tuple[McpToolInfo, ...]:
        toolset = self._relay.current_session_toolsets().get(name)
        if toolset is None:
            return await self._base_registry.list_tools(name)
        async with toolset:
            tools = await toolset.list_tools()
        return tuple(
            McpToolInfo(
                name=get_effective_mcp_tool_name(name, str(tool.name)),
                description=tool.description
                if isinstance(tool.description, str)
                else "",
            )
            for tool in tools
        )

    async def list_tool_schemas(self, name: str) -> tuple[McpToolSchema, ...]:
        toolset = self._relay.current_session_toolsets().get(name)
        if toolset is None:
            return await self._base_registry.list_tool_schemas(name)
        async with toolset:
            tools = await toolset.list_tools()
        return tuple(
            McpToolSchema(
                name=get_effective_mcp_tool_name(name, str(tool.name)),
                description=tool.description
                if isinstance(tool.description, str)
                else "",
                input_schema=(
                    dict(tool.inputSchema) if isinstance(tool.inputSchema, dict) else {}
                ),
            )
            for tool in tools
        )


class AcpMcpRelay:
    def __init__(self) -> None:
        self._request_sender: AcpRequestSender | None = None
        self._notification_sender: AcpNotificationSender | None = None
        self._connections: dict[str, _RelayConnection] = {}
        self._session_active_servers: dict[str, dict[str, str]] = {}
        self._session_specs: dict[str, dict[str, GatewayMcpServerSpec]] = {}
        self._session_toolsets: dict[tuple[str, str], MCPServer] = {}

    def set_outbound(
        self,
        *,
        send_request: AcpRequestSender,
        send_notification: AcpNotificationSender,
    ) -> None:
        self._request_sender = send_request
        self._notification_sender = send_notification

    def bind_session_servers(
        self,
        session_id: str,
        specs: tuple[GatewayMcpServerSpec, ...],
    ) -> None:
        for cache_key in tuple(self._session_toolsets.keys()):
            if cache_key[0] != session_id:
                continue
            if cache_key[1] in {spec.server_id for spec in specs}:
                continue
            del self._session_toolsets[cache_key]
        self._session_specs[session_id] = {spec.server_id: spec for spec in specs}

    def session_scope(self, session_id: str) -> contextlib.AbstractContextManager[None]:
        return _GatewaySessionScope(session_id)

    def current_session_server_names(self) -> tuple[str, ...]:
        session_id = _CURRENT_GATEWAY_SESSION_ID.get()
        if session_id is None:
            return ()
        specs = self._session_specs.get(session_id, {})
        return tuple(sorted(specs.keys()))

    def current_session_specs(self) -> tuple[McpServerSpec, ...]:
        session_id = _CURRENT_GATEWAY_SESSION_ID.get()
        if session_id is None:
            return ()
        specs = self._session_specs.get(session_id, {})
        result: list[McpServerSpec] = []
        for server_id in sorted(specs.keys()):
            result.append(_gateway_spec_to_mcp_spec(specs[server_id]))
        return tuple(result)

    def current_session_spec(self, name: str) -> McpServerSpec:
        for spec in self.current_session_specs():
            if spec.name == name:
                return spec
        raise ValueError(f"Unknown MCP server: {name}")

    def current_session_toolsets(self) -> dict[str, MCPServer]:
        session_id = _CURRENT_GATEWAY_SESSION_ID.get()
        if session_id is None:
            return {}
        specs = self._session_specs.get(session_id, {})
        active = self._session_active_servers.get(session_id, {})
        toolsets: dict[str, MCPServer] = {}
        for server_id, spec in specs.items():
            if spec.transport == "acp":
                connection_id = active.get(server_id)
                if connection_id in self._connections:
                    toolsets[server_id] = self._connections[connection_id].toolset
                continue
            cache_key = (session_id, server_id)
            toolset = self._session_toolsets.get(cache_key)
            if toolset is None:
                toolset = build_mcp_server(_gateway_spec_to_mcp_spec(spec))
                self._session_toolsets[cache_key] = toolset
            toolsets[server_id] = toolset
        return toolsets

    async def open_connection(
        self,
        *,
        session_id: str,
        connection_id: str,
        server_spec: GatewayMcpServerSpec,
    ) -> None:
        self._require_outbound()
        existing_connection_id = self._session_active_servers.setdefault(
            session_id, {}
        ).get(server_spec.server_id)
        if (
            existing_connection_id is not None
            and existing_connection_id != connection_id
        ):
            await self.close_connection(connection_id=existing_connection_id)
        transport = AcpMcpConnectionTransport(
            session_id=session_id,
            connection_id=connection_id,
            send_request=cast(AcpRequestSender, self._request_sender),
            send_notification=cast(AcpNotificationSender, self._notification_sender),
        )
        connection = _RelayConnection(
            session_id=session_id,
            server_id=server_spec.server_id,
            transport=transport,
            toolset=AcpMcpServer(
                transport=transport,
                id=server_spec.server_id,
                tool_prefix=get_mcp_tool_prefix(server_spec.server_id),
            ),
        )
        self._connections[connection_id] = connection
        self._session_active_servers.setdefault(session_id, {})[
            server_spec.server_id
        ] = connection_id

    async def close_connection(self, *, connection_id: str) -> None:
        connection = self._connections.pop(connection_id, None)
        if connection is None:
            return
        await connection.transport.close()
        active = self._session_active_servers.get(connection.session_id)
        if active is not None and active.get(connection.server_id) == connection_id:
            del active[connection.server_id]
            if not active:
                del self._session_active_servers[connection.session_id]

    async def relay_inbound_message(
        self,
        *,
        connection_id: str,
        method: str,
        params: dict[str, JsonValue],
        message_id: JsonRpcId | None,
    ) -> dict[str, JsonValue]:
        connection = self._connections.get(connection_id)
        if connection is None:
            raise KeyError(f"Unknown connection_id: {connection_id}")
        return await connection.transport.handle_inbound_message(
            method=method,
            params=params,
            message_id=message_id,
        )

    def session_server_spec(
        self,
        *,
        session_id: str,
        server_id: str,
    ) -> GatewayMcpServerSpec:
        specs = self._session_specs.get(session_id, {})
        spec = specs.get(server_id)
        if spec is None:
            raise KeyError(f"Unknown MCP server_id: {server_id}")
        return spec

    def _require_outbound(self) -> None:
        if self._request_sender is None or self._notification_sender is None:
            raise RuntimeError("ACP MCP relay outbound transport is not configured")


class AcpMcpConnectionTransport:
    def __init__(
        self,
        *,
        session_id: str,
        connection_id: str,
        send_request: AcpRequestSender,
        send_notification: AcpNotificationSender,
    ) -> None:
        self._session_id = session_id
        self._connection_id = connection_id
        self._send_request = send_request
        self._send_notification = send_notification
        self._connected_write_stream: MemoryObjectSendStream[SessionMessage] | None = (
            None
        )
        self._connected_request_futures: dict[
            JsonRpcId, asyncio.Future[dict[str, JsonValue]]
        ] = {}
        self._message_id = 0
        self._connect_lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def client_streams(
        self,
    ) -> AsyncIterator[
        tuple[
            MemoryObjectReceiveStream[SessionMessage | Exception],
            MemoryObjectSendStream[SessionMessage],
        ]
    ]:
        async with self._connect_lock:
            async with create_client_server_memory_streams() as (
                client_streams,
                server_streams,
            ):
                client_read, client_write = client_streams
                server_read, server_write = server_streams
                self._connected_write_stream = server_write
                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(
                        self._bridge_outbound_messages,
                        server_read,
                        server_write,
                    )
                    try:
                        yield client_read, client_write
                    finally:
                        self._connected_write_stream = None
                        for future in self._connected_request_futures.values():
                            if not future.done():
                                future.cancel()
                        self._connected_request_futures.clear()
                        task_group.cancel_scope.cancel()

    async def close(self) -> None:
        if self._connected_write_stream is None:
            return
        await self._connected_write_stream.aclose()
        self._connected_write_stream = None

    async def handle_inbound_message(
        self,
        *,
        method: str,
        params: dict[str, JsonValue],
        message_id: JsonRpcId | None,
    ) -> dict[str, JsonValue]:
        if self._connected_write_stream is None:
            raise RuntimeError("MCP connection is not active")
        if message_id is None:
            notification = mcp_types.JSONRPCNotification(
                jsonrpc="2.0",
                method=method,
                params=params or None,
            )
            await self._connected_write_stream.send(
                SessionMessage(message=mcp_types.JSONRPCMessage(notification))
            )
            return {}

        internal_id = self._next_message_id()
        future: asyncio.Future[dict[str, JsonValue]] = (
            asyncio.get_running_loop().create_future()
        )
        self._connected_request_futures[internal_id] = future
        request = mcp_types.JSONRPCRequest(
            jsonrpc="2.0",
            id=internal_id,
            method=method,
            params=params or None,
        )
        await self._connected_write_stream.send(
            SessionMessage(message=mcp_types.JSONRPCMessage(request))
        )
        try:
            return await future
        finally:
            self._connected_request_futures.pop(internal_id, None)

    async def _bridge_outbound_messages(
        self,
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception],
        write_stream: MemoryObjectSendStream[SessionMessage],
    ) -> None:
        async with read_stream:
            async for item in read_stream:
                if isinstance(item, Exception):
                    raise item
                raw_message = item.message.root
                if isinstance(raw_message, mcp_types.JSONRPCNotification):
                    await self._send_notification(
                        {
                            "jsonrpc": "2.0",
                            "method": "mcp/message",
                            "params": _build_mcp_message_request(
                                session_id=self._session_id,
                                connection_id=self._connection_id,
                                method=raw_message.method,
                                params=_json_object(raw_message.params),
                            ),
                        }
                    )
                    continue
                if isinstance(raw_message, mcp_types.JSONRPCRequest):
                    response = await self._send_request(
                        "mcp/message",
                        _build_mcp_message_request(
                            session_id=self._session_id,
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
                if isinstance(raw_message, mcp_types.JSONRPCResponse):
                    future = self._connected_request_futures.get(raw_message.id)
                    if future is not None and not future.done():
                        future.set_result(dict(raw_message.result))
                    continue
                if isinstance(raw_message, mcp_types.JSONRPCError):
                    future = self._connected_request_futures.get(raw_message.id)
                    if future is not None and not future.done():
                        future.set_exception(RuntimeError(raw_message.error.message))

    def _next_message_id(self) -> int:
        self._message_id += 1
        return self._message_id


class _RelayConnection:
    def __init__(
        self,
        *,
        session_id: str,
        server_id: str,
        transport: AcpMcpConnectionTransport,
        toolset: MCPServer,
    ) -> None:
        self.session_id = session_id
        self.server_id = server_id
        self.transport = transport
        self.toolset = toolset


class AcpMcpServer(MCPServer):
    def __init__(
        self,
        *,
        transport: AcpMcpConnectionTransport,
        id: str,
        tool_prefix: str | None = None,
    ) -> None:
        super().__init__(id=id, tool_prefix=tool_prefix)
        self._transport = transport

    @contextlib.asynccontextmanager
    async def client_streams(
        self,
    ) -> AsyncIterator[
        tuple[
            MemoryObjectReceiveStream[SessionMessage | Exception],
            MemoryObjectSendStream[SessionMessage],
        ]
    ]:
        async with self._transport.client_streams() as streams:
            yield streams


class _GatewaySessionScope(contextlib.AbstractContextManager[None]):
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._token: Token[str | None] | None = None

    def __enter__(self) -> None:
        self._token = _CURRENT_GATEWAY_SESSION_ID.set(self._session_id)
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._token is not None:
            _CURRENT_GATEWAY_SESSION_ID.reset(self._token)
        return None


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


def _gateway_spec_to_mcp_spec(spec: GatewayMcpServerSpec) -> McpServerSpec:
    server_config = dict(spec.config)
    if "transport" not in server_config:
        server_config["transport"] = spec.transport
    if spec.transport == "acp":
        server_config["id"] = spec.server_id
    return McpServerSpec(
        name=spec.server_id,
        config={"mcpServers": {spec.server_id: dict(server_config)}},
        server_config=server_config,
        source=McpConfigScope.SESSION,
    )


def _jsonrpc_message_from_acp_response(
    *,
    raw_request_id: JsonRpcId,
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


def _json_object(value: object) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return cast(dict[str, JsonValue], value)
    return {}
