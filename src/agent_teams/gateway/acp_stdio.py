# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import BinaryIO
from uuid import uuid4

from pydantic import JsonValue

from agent_teams.gateway.gateway_models import GatewayChannelType, GatewayMcpServerSpec
from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.sessions import SessionService
from agent_teams.sessions.runs.enums import ApprovalMode, RunEventType
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_models import IntentInput, RunEvent


type JsonRpcId = str | int

type AcpNotifier = Callable[[dict[str, JsonValue]], Awaitable[None]]


class AcpProtocolError(ValueError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AcpGatewayServer:
    def __init__(
        self,
        *,
        gateway_session_service: GatewaySessionService,
        session_service: SessionService,
        run_service: RunManager,
        notify: AcpNotifier,
    ) -> None:
        self._gateway_session_service = gateway_session_service
        self._session_service = session_service
        self._run_service = run_service
        self._notify = notify
        self._active_runs: dict[str, str] = {}

    def set_notify(self, notify: AcpNotifier) -> None:
        self._notify = notify

    async def handle_jsonrpc_message(
        self,
        message: dict[str, JsonValue],
    ) -> dict[str, JsonValue] | None:
        message_id = _optional_id(message)
        method = _required_method(message)
        params = _params_object(message)

        if message_id is None:
            await self._handle_notification(method, params)
            return None

        try:
            result = await self._handle_request(method, params, message_id)
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": result,
            }
        except AcpProtocolError as exc:
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": -32000,
                    "message": str(exc) or exc.__class__.__name__,
                },
            }

    async def _handle_request(
        self,
        method: str,
        params: dict[str, JsonValue],
        message_id: JsonRpcId,
    ) -> dict[str, JsonValue]:
        if method == "initialize":
            return self._initialize_result(params)
        if method == "session/new":
            return self._create_session(params)
        if method == "session/load":
            return await self._load_session(params)
        if method == "session/prompt":
            return await self._prompt_session(params, message_id)
        if method == "session/cancel":
            return self._cancel_session(params)
        if method == "mcp/connect":
            return self._mcp_connect(params)
        if method == "mcp/message":
            raise AcpProtocolError(
                -32001,
                "MCP-over-ACP relay is not wired into the runtime yet.",
            )
        if method == "mcp/disconnect":
            return self._mcp_disconnect(params)
        raise AcpProtocolError(-32601, f"Method not found: {method}")

    async def _handle_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        if method == "session/cancel":
            _ = self._cancel_session(params)
            return
        if method == "initialized":
            return
        raise AcpProtocolError(-32601, f"Method not found: {method}")

    def _initialize_result(self, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        protocol_version = params.get("protocolVersion")
        resolved_protocol_version = 1
        if isinstance(protocol_version, int) and protocol_version > 0:
            resolved_protocol_version = protocol_version
        return {
            "protocolVersion": resolved_protocol_version,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {
                    "audio": False,
                    "embeddedContext": False,
                    "image": False,
                },
                "mcpCapabilities": {
                    "acp": False,
                    "http": False,
                    "sse": False,
                },
            },
            "agentInfo": {
                "name": "agent-teams",
                "version": "0.1.0",
            },
        }

    def _create_session(self, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        cwd = _optional_str(params, "cwd")
        capabilities = _optional_object(params, "capabilities")
        mcp_servers = _parse_mcp_servers(params.get("mcpServers"))
        record = self._gateway_session_service.create_session(
            channel_type=GatewayChannelType.ACP_STDIO,
            cwd=cwd,
            capabilities=capabilities,
            session_mcp_servers=mcp_servers,
        )
        return {"sessionId": record.gateway_session_id}

    async def _load_session(
        self,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        record = self._gateway_session_service.get_session(gateway_session_id)
        messages = self._session_service.get_session_messages(
            record.internal_session_id
        )
        for item in messages:
            role = str(item.get("role") or "")
            message = item.get("message")
            text = _message_payload_to_text(message)
            if not text:
                continue
            if role == "user":
                await self._publish_session_update(
                    gateway_session_id,
                    {
                        "sessionUpdate": "user_message_chunk",
                        "messageId": f"msg_{uuid4().hex[:12]}",
                        "content": {
                            "type": "text",
                            "text": text,
                        },
                    },
                )
                continue
            await self._publish_session_update(
                gateway_session_id,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": f"msg_{uuid4().hex[:12]}",
                    "content": {
                        "type": "text",
                        "text": text,
                    },
                },
            )
        return {"sessionId": gateway_session_id}

    async def _prompt_session(
        self,
        params: dict[str, JsonValue],
        message_id: JsonRpcId,
    ) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        prompt_blocks = _required_list(params, "prompt")
        prompt_text = _prompt_blocks_to_text(prompt_blocks)
        if not prompt_text:
            raise AcpProtocolError(
                -32602, "prompt must contain at least one text block"
            )
        user_message_id = (
            _optional_str(params, "messageId") or f"msg_{uuid4().hex[:12]}"
        )

        record = self._gateway_session_service.get_session(gateway_session_id)
        run_id, _ = self._run_service.create_run(
            IntentInput(
                session_id=record.internal_session_id,
                intent=prompt_text,
                approval_mode=ApprovalMode.YOLO,
            )
        )
        self._active_runs[gateway_session_id] = run_id
        _ = self._gateway_session_service.bind_active_run(gateway_session_id, run_id)
        await self._publish_session_update(
            gateway_session_id,
            {
                "sessionUpdate": "user_message_chunk",
                "messageId": user_message_id,
                "content": {
                    "type": "text",
                    "text": prompt_text,
                },
            },
        )

        agent_message_id = f"msg_{uuid4().hex[:12]}"
        thought_message_id = f"msg_{uuid4().hex[:12]}"
        stop_reason = "end_turn"
        terminal_error: str | None = None

        async for event in self._run_service.stream_run_events(run_id):
            maybe_stop_reason, maybe_error = await self._map_run_event(
                gateway_session_id=gateway_session_id,
                event=event,
                agent_message_id=agent_message_id,
                thought_message_id=thought_message_id,
            )
            if maybe_stop_reason is not None:
                stop_reason = maybe_stop_reason
            if maybe_error is not None:
                terminal_error = maybe_error

        self._active_runs.pop(gateway_session_id, None)
        _ = self._gateway_session_service.bind_active_run(gateway_session_id, None)
        if terminal_error is not None:
            raise AcpProtocolError(-32000, terminal_error)
        return {
            "stopReason": stop_reason,
            "userMessageId": user_message_id,
            "usage": self._usage_for_run(run_id),
            "_meta": {
                "requestId": str(message_id),
                "runId": run_id,
            },
        }

    async def _map_run_event(
        self,
        *,
        gateway_session_id: str,
        event: RunEvent,
        agent_message_id: str,
        thought_message_id: str,
    ) -> tuple[str | None, str | None]:
        payload = _load_payload(event.payload_json)
        if event.event_type == RunEventType.TEXT_DELTA:
            text = _optional_str(payload, "text")
            if text:
                await self._publish_session_update(
                    gateway_session_id,
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "messageId": agent_message_id,
                        "content": {
                            "type": "text",
                            "text": text,
                        },
                    },
                )
            return None, None
        if event.event_type == RunEventType.THINKING_DELTA:
            text = _optional_str(payload, "text")
            if text:
                await self._publish_session_update(
                    gateway_session_id,
                    {
                        "sessionUpdate": "agent_thought_chunk",
                        "messageId": thought_message_id,
                        "content": {
                            "type": "text",
                            "text": text,
                        },
                    },
                )
            return None, None
        if event.event_type == RunEventType.TOOL_CALL:
            tool_call_id = (
                _optional_str(payload, "tool_call_id") or f"tool_{uuid4().hex[:12]}"
            )
            raw_input = payload.get("args")
            update: dict[str, JsonValue] = {
                "sessionUpdate": "tool_call",
                "toolCallId": tool_call_id,
                "title": _optional_str(payload, "tool_name") or "tool",
                "kind": "function",
                "status": "in_progress",
            }
            if isinstance(raw_input, dict):
                update["rawInput"] = raw_input
            await self._publish_session_update(gateway_session_id, update)
            return None, None
        if event.event_type == RunEventType.TOOL_RESULT:
            tool_call_id = (
                _optional_str(payload, "tool_call_id") or f"tool_{uuid4().hex[:12]}"
            )
            result_text = json.dumps(
                payload.get("result"), ensure_ascii=False, default=str
            )
            status = "failed" if payload.get("error") is True else "completed"
            await self._publish_session_update(
                gateway_session_id,
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": tool_call_id,
                    "status": status,
                    "content": [
                        {
                            "type": "content",
                            "content": {
                                "type": "text",
                                "text": result_text,
                            },
                        }
                    ],
                },
            )
            return None, None
        if event.event_type == RunEventType.RUN_STOPPED:
            return "cancelled", None
        if event.event_type == RunEventType.RUN_FAILED:
            error_text = _optional_str(payload, "error") or "Run failed"
            return "end_turn", error_text
        if event.event_type == RunEventType.RUN_COMPLETED:
            return "end_turn", None
        return None, None

    def _cancel_session(self, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        run_id = self._active_runs.get(gateway_session_id)
        if run_id is not None:
            self._run_service.stop_run(run_id)
        return {"status": "ok"}

    def _mcp_connect(self, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        server_id = _required_str(params, "serverId")
        connection = self._gateway_session_service.open_mcp_connection(
            gateway_session_id=gateway_session_id,
            server_id=server_id,
        )
        return {
            "connectionId": connection.connection_id,
            "serverId": connection.server_id,
            "status": connection.status.value,
        }

    def _mcp_disconnect(self, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        gateway_session_id = _required_str(params, "sessionId")
        connection_id = _required_str(params, "connectionId")
        _ = self._gateway_session_service.close_mcp_connection(
            gateway_session_id=gateway_session_id,
            connection_id=connection_id,
        )
        return {"status": "closed", "connectionId": connection_id}

    def _usage_for_run(self, run_id: str) -> dict[str, JsonValue]:
        try:
            usage = self._session_service.get_token_usage_by_run(run_id)
        except Exception:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "thought_tokens": 0,
                "cached_read_tokens": 0,
                "cached_write_tokens": 0,
                "total_tokens": 0,
            }
        return {
            "input_tokens": usage.total_input_tokens,
            "output_tokens": usage.total_output_tokens,
            "thought_tokens": usage.total_reasoning_output_tokens,
            "cached_read_tokens": usage.total_cached_input_tokens,
            "cached_write_tokens": 0,
            "total_tokens": usage.total_tokens,
        }

    async def _publish_session_update(
        self,
        gateway_session_id: str,
        update: dict[str, JsonValue],
    ) -> None:
        await self._notify(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": gateway_session_id,
                    "update": update,
                },
            }
        )


class AcpStdioRuntime:
    def __init__(
        self,
        *,
        server: AcpGatewayServer,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
    ) -> None:
        self._server = server
        self._input_stream = input_stream
        self._output_stream = output_stream
        self._write_lock = asyncio.Lock()

    async def serve_forever(self) -> None:
        tasks: set[asyncio.Task[None]] = set()
        while True:
            raw_message = await asyncio.to_thread(
                _read_message_bytes, self._input_stream
            )
            if raw_message is None:
                break
            if not raw_message:
                continue
            try:
                parsed = json.loads(raw_message.decode("utf-8"))
            except json.JSONDecodeError:
                await self.send_message(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Parse error",
                        },
                    }
                )
                continue
            if not isinstance(parsed, dict):
                await self.send_message(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": "Invalid Request",
                        },
                    }
                )
                continue
            task = asyncio.create_task(self._handle_message(parsed))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_message(self, parsed: dict[str, JsonValue]) -> None:
        response = await self._server.handle_jsonrpc_message(parsed)
        if response is None:
            return
        await self.send_message(response)

    async def send_message(self, message: dict[str, JsonValue]) -> None:
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        async with self._write_lock:
            await asyncio.to_thread(self._output_stream.write, header)
            await asyncio.to_thread(self._output_stream.write, payload)
            await asyncio.to_thread(self._output_stream.flush)


def _read_message_bytes(stream: BinaryIO) -> bytes | None:
    while True:
        first_line = stream.readline()
        if not first_line:
            return None
        if first_line in {b"\r\n", b"\n"}:
            continue
        stripped = first_line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith(b"content-length:"):
            _, _, raw_length = stripped.partition(b":")
            length = int(raw_length.strip())
            while True:
                header_line = stream.readline()
                if not header_line:
                    return None
                if header_line in {b"\r\n", b"\n"}:
                    break
            return stream.read(length)
        return stripped


def _optional_id(message: dict[str, JsonValue]) -> JsonRpcId | None:
    raw = message.get("id")
    if isinstance(raw, (str, int)):
        return raw
    return None


def _required_method(message: dict[str, JsonValue]) -> str:
    raw_method = message.get("method")
    if isinstance(raw_method, str) and raw_method.strip():
        return raw_method.strip()
    raise AcpProtocolError(-32600, "Invalid Request")


def _params_object(message: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw_params = message.get("params")
    if raw_params is None:
        return {}
    if isinstance(raw_params, dict):
        return raw_params
    raise AcpProtocolError(-32602, "params must be an object")


def _required_str(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise AcpProtocolError(-32602, f"{key} must be a non-empty string")


def _optional_str(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _optional_object(payload: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    value = payload.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise AcpProtocolError(-32602, f"{key} must be an object")


def _required_list(payload: dict[str, JsonValue], key: str) -> list[JsonValue]:
    value = payload.get(key)
    if isinstance(value, list):
        return value
    raise AcpProtocolError(-32602, f"{key} must be a list")


def _parse_mcp_servers(raw_value: JsonValue | None) -> tuple[GatewayMcpServerSpec, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, list):
        raise AcpProtocolError(-32602, "mcpServers must be a list")
    result: list[GatewayMcpServerSpec] = []
    for index, item in enumerate(raw_value):
        if not isinstance(item, dict):
            raise AcpProtocolError(-32602, "mcpServers items must be objects")
        raw_name = item.get("name")
        raw_transport = item.get("transport")
        raw_id = item.get("id")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise AcpProtocolError(-32602, f"mcpServers[{index}].name must be a string")
        if not isinstance(raw_transport, str) or not raw_transport.strip():
            raise AcpProtocolError(
                -32602,
                f"mcpServers[{index}].transport must be a string",
            )
        server_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else raw_name
        result.append(
            GatewayMcpServerSpec(
                server_id=server_id,
                name=raw_name.strip(),
                transport=raw_transport.strip(),
                config={str(key): value for key, value in item.items()},
            )
        )
    return tuple(result)


def _prompt_blocks_to_text(prompt_blocks: list[JsonValue]) -> str:
    collected: list[str] = []
    for item in prompt_blocks:
        if not isinstance(item, dict):
            continue
        block_type = item.get("type")
        if block_type == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                collected.append(text.strip())
            continue
        if block_type == "resource_link":
            uri = item.get("uri")
            if isinstance(uri, str) and uri.strip():
                collected.append(f"Resource: {uri.strip()}")
    return "\n\n".join(collected).strip()


def _message_payload_to_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    collected: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_kind = str(part.get("part_kind") or "")
        if part_kind in {"user-prompt", "text", "thinking", "system-prompt"}:
            content = part.get("content")
            if isinstance(content, str) and content.strip():
                collected.append(content.strip())
    return "\n\n".join(collected).strip()


def _load_payload(raw_payload: str) -> dict[str, JsonValue]:
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


async def run_acp_stdio_server(server: AcpGatewayServer) -> None:
    runtime = AcpStdioRuntime(
        server=server,
        input_stream=sys.stdin.buffer,
        output_stream=sys.stdout.buffer,
    )
    server.set_notify(runtime.send_message)
    await runtime.serve_forever()
