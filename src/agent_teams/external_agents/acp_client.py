# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx
from pydantic import JsonValue

from agent_teams.external_agents.models import (
    CustomTransportConfig,
    ExternalAgentConfig,
    ExternalAgentTestResult,
    StdioTransportConfig,
    StreamableHttpTransportConfig,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.net.clients import create_async_http_client

JsonRpcId = str | int
AcpNotificationHandler = Callable[[dict[str, JsonValue]], Awaitable[None]]

LOGGER = get_logger(__name__)


class AcpProtocolError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AcpTransportClient(Protocol):
    async def start(self) -> None: ...

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]: ...

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None: ...

    async def close(self) -> None: ...


class CustomAcpTransportAdapter(Protocol):
    def build_transport(
        self,
        *,
        config: CustomTransportConfig,
        on_message: AcpNotificationHandler,
    ) -> AcpTransportClient: ...


_CUSTOM_TRANSPORT_ADAPTERS: dict[str, CustomAcpTransportAdapter] = {}


def register_custom_transport_adapter(
    adapter_id: str,
    adapter: CustomAcpTransportAdapter,
) -> None:
    normalized_adapter_id = str(adapter_id or "").strip()
    if not normalized_adapter_id:
        raise ValueError("adapter_id is required")
    _CUSTOM_TRANSPORT_ADAPTERS[normalized_adapter_id] = adapter


def build_acp_transport(
    *,
    config: ExternalAgentConfig,
    on_message: AcpNotificationHandler,
    runtime_cwd: str | None = None,
) -> AcpTransportClient:
    if isinstance(config.transport, StdioTransportConfig):
        return StdioAcpTransportClient(
            config=config.transport,
            on_message=on_message,
            runtime_cwd=runtime_cwd,
        )
    if isinstance(config.transport, StreamableHttpTransportConfig):
        return HttpAcpTransportClient(
            config=config.transport,
            on_message=on_message,
        )
    if isinstance(config.transport, CustomTransportConfig):
        adapter = _CUSTOM_TRANSPORT_ADAPTERS.get(config.transport.adapter_id)
        if adapter is None:
            raise RuntimeError(
                "No custom ACP transport adapter is registered for "
                f"{config.transport.adapter_id}"
            )
        return adapter.build_transport(
            config=config.transport,
            on_message=on_message,
        )
    raise RuntimeError(
        f"Unsupported external agent transport: {config.transport.transport.value}"
    )


async def probe_acp_agent(config: ExternalAgentConfig) -> ExternalAgentTestResult:
    async def _ignore_message(_message: dict[str, JsonValue]) -> None:
        return None

    transport = build_acp_transport(config=config, on_message=_ignore_message)
    try:
        await transport.start()
        result = await transport.send_request(
            "initialize",
            {"protocolVersion": 1},
        )
        protocol_version = _as_int(result.get("protocolVersion"))
        agent_info = _as_object(result.get("agentInfo"))
        return ExternalAgentTestResult(
            ok=True,
            message="External ACP agent is reachable.",
            protocol_version=protocol_version,
            agent_name=_as_str(agent_info.get("name")),
            agent_version=_as_str(agent_info.get("version")),
        )
    except Exception as exc:
        return ExternalAgentTestResult(
            ok=False,
            message=str(exc) or exc.__class__.__name__,
        )
    finally:
        await transport.close()


class StdioAcpTransportClient:
    def __init__(
        self,
        *,
        config: StdioTransportConfig,
        on_message: AcpNotificationHandler,
        runtime_cwd: str | None = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._runtime_cwd = runtime_cwd
        self._process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._next_request_id = 0
        self._pending: dict[JsonRpcId, asyncio.Future[dict[str, JsonValue]]] = {}

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        env = os.environ.copy()
        for item in self._config.env:
            if item.value is not None:
                env[item.name] = item.value
        self._process = await asyncio.create_subprocess_exec(
            self._config.command,
            *self._config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._runtime_cwd,
            env=env,
        )
        self._read_task = asyncio.create_task(self._read_stdout_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr_loop())

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self.start()
        self._next_request_id += 1
        request_id = self._next_request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, JsonValue]] = loop.create_future()
        self._pending[request_id] = future
        await self._send_raw(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        await self.start()
        await self._send_raw(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    async def close(self) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.cancel()
        if self._read_task is not None:
            self._read_task.cancel()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
        self._process = None
        self._read_task = None
        self._stderr_task = None

    async def _send_raw(self, message: dict[str, JsonValue]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("ACP stdio transport is not started")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        async with self._write_lock:
            self._process.stdin.write(payload.encode("utf-8") + b"\n")
            await self._process.stdin.drain()

    async def _read_stdout_loop(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        while True:
            raw_message = await _read_next_stdio_message(self._process.stdout)
            if raw_message is None:
                break
            if not raw_message:
                continue
            try:
                payload = json.loads(raw_message.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            await self._handle_payload(payload)

    async def _drain_stderr_loop(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while True:
            raw_line = await self._process.stderr.readline()
            if not raw_line:
                break
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            log_event(
                LOGGER,
                logging.DEBUG,
                event="external_agent.stdio.stderr",
                message="External ACP agent wrote to stderr",
                payload={"line": text[:500]},
            )

    async def _handle_payload(self, payload: dict[str, JsonValue]) -> None:
        response_id = _optional_id(payload)
        if response_id is not None and ("result" in payload or "error" in payload):
            future = self._pending.get(response_id)
            if future is None or future.done():
                return
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                future.set_exception(
                    AcpProtocolError(
                        code=_as_int(error_payload.get("code")) or -32000,
                        message=_as_str(error_payload.get("message"))
                        or "ACP request failed",
                    )
                )
                return
            result = _as_object(payload.get("result"))
            future.set_result(result)
            return

        method = _as_str(payload.get("method"))
        if not method:
            return
        params = _as_object(payload.get("params"))
        if response_id is not None:
            await self._send_raw(
                {
                    "jsonrpc": "2.0",
                    "id": response_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )
            return
        await self._on_message(
            {
                "method": method,
                "params": params,
            }
        )


class HttpAcpTransportClient:
    def __init__(
        self,
        *,
        config: StreamableHttpTransportConfig,
        on_message: AcpNotificationHandler,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._client: httpx.AsyncClient | None = None
        self._next_request_id = 0

    async def start(self) -> None:
        if self._client is not None and not self._client.is_closed:
            return
        self._client = create_async_http_client(
            ssl_verify=self._config.ssl_verify,
        )

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self.start()
        self._next_request_id += 1
        request_id = self._next_request_id
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        if self._client is None:
            raise RuntimeError("ACP HTTP transport is not started")
        response_message: dict[str, JsonValue] | None = None
        headers = {
            item.name: item.value
            for item in self._config.headers
            if item.value is not None
        }
        async with self._client.stream(
            "POST",
            self._config.url,
            json=message,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                line = raw_line.strip()
                if not line or line.startswith("event:"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line:
                    continue
                parsed = json.loads(line)
                if not isinstance(parsed, dict):
                    continue
                if _optional_id(parsed) == request_id and (
                    "result" in parsed or "error" in parsed
                ):
                    response_message = parsed
                    continue
                method_name = _as_str(parsed.get("method"))
                if method_name:
                    await self._on_message(
                        {
                            "method": method_name,
                            "params": _as_object(parsed.get("params")),
                        }
                    )
        if response_message is None:
            raise RuntimeError(
                "ACP HTTP transport did not return a JSON-RPC response for the request"
            )
        error_payload = response_message.get("error")
        if isinstance(error_payload, dict):
            raise AcpProtocolError(
                code=_as_int(error_payload.get("code")) or -32000,
                message=_as_str(error_payload.get("message")) or "ACP request failed",
            )
        return _as_object(response_message.get("result"))

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        await self.start()
        if self._client is None:
            raise RuntimeError("ACP HTTP transport is not started")
        headers = {
            item.name: item.value
            for item in self._config.headers
            if item.value is not None
        }
        response = await self._client.post(
            self._config.url,
            json={
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            },
            headers=headers,
        )
        response.raise_for_status()

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None


async def _read_next_stdio_message(
    stream: asyncio.StreamReader,
) -> bytes | None:
    first_line = await stream.readline()
    if not first_line:
        return None
    if first_line.startswith(b"Content-Length:"):
        try:
            content_length = int(first_line.partition(b":")[2].strip())
        except ValueError as exc:  # pragma: no cover - defensive parse guard
            raise RuntimeError("Invalid Content-Length header from ACP agent") from exc
        while True:
            header_line = await stream.readline()
            if not header_line or header_line in (b"\n", b"\r\n"):
                break
        return await stream.readexactly(content_length)
    return first_line.rstrip(b"\r\n")


def _optional_id(payload: dict[str, JsonValue]) -> JsonRpcId | None:
    raw_id = payload.get("id")
    if isinstance(raw_id, (str, int)):
        return raw_id
    return None


def _as_object(value: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _as_str(value: JsonValue | None) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def _as_int(value: JsonValue | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
