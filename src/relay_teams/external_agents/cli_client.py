# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams._version import __version__
from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    ExternalAgentTestResult,
    StdioTransportConfig,
)
from relay_teams.logger import get_logger, log_event

JsonRpcId = str | int

_CLI_INITIALIZE_TIMEOUT_SECONDS = 10.0
_CODEX_APP_SERVER_SUBCOMMAND = "app-server"
_CODEX_APP_SERVER_LISTENER = "stdio://"
_CODEX_APP_SERVER_VALUE_OPTIONS = {
    "-c",
    "--config",
    "--disable",
    "--enable",
    "--listen",
    "--ws-audience",
    "--ws-auth",
    "--ws-issuer",
    "--ws-max-clock-skew-seconds",
    "--ws-shared-secret-file",
    "--ws-token-file",
    "--ws-token-sha256",
}
_CODEX_LEGACY_EXEC_VALUE_OPTIONS = {
    "--add-dir",
    "--ask-for-approval",
    "--cd",
    "--color",
    "--local-provider",
    "--model",
    "--output-last-message",
    "--output-schema",
    "--profile",
    "--remote",
    "--remote-auth-token-env",
    "-a",
    "-C",
    "-i",
    "-m",
    "-o",
    "-p",
    "-s",
    "--sandbox",
}
_CODEX_LEGACY_EXEC_FLAG_OPTIONS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
}
_CODEX_LEGACY_EXEC_SUBCOMMANDS = {"exec", "e"}

LOGGER = get_logger(__name__)


class CliAgentError(RuntimeError):
    pass


class CliJsonRpcError(CliAgentError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _CliJsonRpcNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    params: dict[str, JsonValue] = Field(default_factory=dict)


async def probe_cli_agent(config: ExternalAgentConfig) -> ExternalAgentTestResult:
    client: _StdioCliJsonRpcClient | None = None
    try:
        transport = _stdio_transport(config)
        command: str = str(transport.command)
        if not _cli_command_exists(command):
            raise CliAgentError(f"CLI command not found: {command}")
        client = _StdioCliJsonRpcClient(
            command=command,
            args=_build_command_args(transport=transport),
            runtime_cwd=None,
            transport=transport,
        )
        result = await _initialize_cli_runtime(
            client=client,
            timeout_seconds=_CLI_INITIALIZE_TIMEOUT_SECONDS,
        )
        user_agent = _as_str(result.get("userAgent"))
        return ExternalAgentTestResult(
            ok=True,
            message="External CLI agent runtime is reachable over stdio JSON-RPC.",
            protocol=ExternalAgentProtocol.CLI,
            protocol_version_text="stdio-jsonrpc",
            agent_name=Path(command).name,
            agent_version=user_agent,
        )
    except Exception as exc:
        return ExternalAgentTestResult(
            ok=False,
            message=str(exc) or exc.__class__.__name__,
            protocol=ExternalAgentProtocol.CLI,
        )
    finally:
        if client is not None:
            await client.close()


async def run_cli_agent_prompt(
    *,
    config: ExternalAgentConfig,
    prompt: str,
    runtime_cwd: Path,
    timeout_seconds: float,
) -> str:
    transport = _stdio_transport(config)
    client = _StdioCliJsonRpcClient(
        command=transport.command,
        args=_build_command_args(transport=transport),
        runtime_cwd=runtime_cwd,
        transport=transport,
    )
    try:
        await _initialize_cli_runtime(
            client=client,
            timeout_seconds=min(timeout_seconds, _CLI_INITIALIZE_TIMEOUT_SECONDS),
        )
        thread_id = await _start_cli_thread(
            client=client,
            runtime_cwd=runtime_cwd,
            timeout_seconds=timeout_seconds,
        )
        turn_id = await _start_cli_turn(
            client=client,
            prompt=prompt,
            runtime_cwd=runtime_cwd,
            thread_id=thread_id,
            timeout_seconds=timeout_seconds,
        )
        return await _wait_for_cli_turn_output(
            client=client,
            thread_id=thread_id,
            turn_id=turn_id,
            timeout_seconds=timeout_seconds,
        )
    finally:
        await client.close()


def _stdio_transport(config: ExternalAgentConfig) -> StdioTransportConfig:
    if not isinstance(config.transport, StdioTransportConfig):
        raise CliAgentError("CLI agent runtimes require stdio transport")
    return config.transport


def _cli_command_exists(command: str) -> bool:
    if "/" in command or "\\" in command:
        return Path(command).exists()
    for directory in os.get_exec_path():
        candidate = Path(directory) / command
        if _executable_path_exists(candidate):
            return True
        if os.name == "nt":
            for suffix in os.environ.get("PATHEXT", "").split(os.pathsep):
                if suffix and _executable_path_exists(
                    Path(directory) / f"{command}{suffix}"
                ):
                    return True
    return False


def _executable_path_exists(path: Path) -> bool:
    if not path.exists():
        return False
    if os.name == "nt":
        return path.is_file()
    return os.access(path, os.X_OK)


def _build_command_args(*, transport: StdioTransportConfig) -> tuple[str, ...]:
    if not _is_codex_command(transport.command):
        return transport.args
    return _build_codex_app_server_args(transport.args)


def _build_codex_app_server_args(args: tuple[str, ...]) -> tuple[str, ...]:
    first_positional = _first_codex_positional_arg(args)
    if first_positional == _CODEX_APP_SERVER_SUBCOMMAND:
        return _ensure_codex_stdio_listener(args)
    migrated_options = _migrate_legacy_codex_args(args)
    return (
        _CODEX_APP_SERVER_SUBCOMMAND,
        "--listen",
        _CODEX_APP_SERVER_LISTENER,
        *migrated_options,
    )


def _ensure_codex_stdio_listener(args: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    skip_next = False
    replaced = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--listen":
            normalized.extend(("--listen", _CODEX_APP_SERVER_LISTENER))
            skip_next = True
            replaced = True
            continue
        if arg.startswith("--listen="):
            normalized.extend(("--listen", _CODEX_APP_SERVER_LISTENER))
            replaced = True
            continue
        normalized.append(arg)
    if replaced:
        return tuple(normalized)
    return tuple(normalized) + ("--listen", _CODEX_APP_SERVER_LISTENER)


def _migrate_legacy_codex_args(args: tuple[str, ...]) -> tuple[str, ...]:
    migrated: list[str] = []
    skip_next = False
    copy_next = False
    for arg in args:
        if copy_next:
            migrated.append(arg)
            copy_next = False
            continue
        if skip_next:
            skip_next = False
            continue
        option_name = arg.split("=", maxsplit=1)[0]
        if arg == "--":
            break
        if arg in _CODEX_LEGACY_EXEC_SUBCOMMANDS:
            continue
        if arg in _CODEX_LEGACY_EXEC_FLAG_OPTIONS:
            continue
        if option_name == "--listen":
            skip_next = "=" not in arg
            continue
        if option_name in _CODEX_APP_SERVER_VALUE_OPTIONS:
            migrated.append(arg)
            copy_next = "=" not in arg
            continue
        if option_name in _CODEX_LEGACY_EXEC_VALUE_OPTIONS:
            skip_next = "=" not in arg
            continue
        if arg == "--analytics-default-enabled":
            migrated.append(arg)
    return tuple(migrated)


def _is_codex_command(command: str) -> bool:
    name = Path(command.replace("\\", "/")).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name == "codex" or name.startswith("codex-")


def _first_codex_positional_arg(args: tuple[str, ...]) -> str | None:
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            return None
        if arg.startswith("-"):
            if _codex_option_consumes_value(arg):
                skip_next = True
            continue
        return arg
    return None


def _codex_option_consumes_value(arg: str) -> bool:
    option_name = arg.split("=", maxsplit=1)[0]
    value_options = _CODEX_APP_SERVER_VALUE_OPTIONS | _CODEX_LEGACY_EXEC_VALUE_OPTIONS
    return option_name in value_options and "=" not in arg


async def _initialize_cli_runtime(
    *,
    client: _StdioCliJsonRpcClient,
    timeout_seconds: float,
) -> dict[str, JsonValue]:
    result = await asyncio.wait_for(
        client.send_request(
            "initialize",
            {
                "clientInfo": {
                    "name": "relay-teams-runtime",
                    "version": __version__,
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            },
        ),
        timeout=timeout_seconds,
    )
    await client.send_notification("initialized")
    return result


async def _start_cli_thread(
    *,
    client: _StdioCliJsonRpcClient,
    runtime_cwd: Path,
    timeout_seconds: float,
) -> str:
    result = await asyncio.wait_for(
        client.send_request(
            "thread/start",
            {
                "cwd": str(runtime_cwd),
                "ephemeral": True,
                "approvalPolicy": "never",
            },
        ),
        timeout=timeout_seconds,
    )
    thread = _as_object(result.get("thread"))
    thread_id = _as_str(thread.get("id"))
    if thread_id is None:
        raise CliAgentError("CLI JSON-RPC runtime did not return a thread id")
    return thread_id


async def _start_cli_turn(
    *,
    client: _StdioCliJsonRpcClient,
    prompt: str,
    runtime_cwd: Path,
    thread_id: str,
    timeout_seconds: float,
) -> str:
    result = await asyncio.wait_for(
        client.send_request(
            "turn/start",
            {
                "threadId": thread_id,
                "cwd": str(runtime_cwd),
                "approvalPolicy": "never",
                "input": [
                    {
                        "type": "text",
                        "text": prompt,
                    }
                ],
            },
        ),
        timeout=timeout_seconds,
    )
    turn = _as_object(result.get("turn"))
    turn_id = _as_str(turn.get("id"))
    if turn_id is None:
        raise CliAgentError("CLI JSON-RPC runtime did not return a turn id")
    return turn_id


async def _wait_for_cli_turn_output(
    *,
    client: _StdioCliJsonRpcClient,
    thread_id: str,
    turn_id: str,
    timeout_seconds: float,
) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    delta_parts: list[str] = []
    completed_messages: list[str] = []
    turn_error: str | None = None
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise CliAgentError(
                f"External CLI agent timed out after {timeout_seconds:g} seconds"
            )
        notification = await asyncio.wait_for(
            client.next_notification(),
            timeout=remaining,
        )
        if not _matches_turn(
            params=notification.params,
            thread_id=thread_id,
            turn_id=turn_id,
        ):
            continue
        if notification.method == "item/agentMessage/delta":
            delta = _as_text(notification.params.get("delta"))
            if delta is not None:
                delta_parts.append(delta)
            continue
        if notification.method == "item/completed":
            item = _as_object(notification.params.get("item"))
            if _as_str(item.get("type")) == "agentMessage":
                text = _as_text(item.get("text"))
                if text is not None:
                    completed_messages.append(text)
            continue
        if notification.method == "error":
            will_retry = notification.params.get("willRetry")
            if will_retry is not True:
                turn_error = _turn_error_message(notification.params)
            continue
        if notification.method != "turn/completed":
            continue
        status = _turn_status(notification.params)
        if status == "failed":
            raise CliAgentError(
                _turn_error_message(notification.params)
                or turn_error
                or "External CLI agent turn failed"
            )
        output = "".join(delta_parts).strip()
        if not output:
            output = "\n\n".join(completed_messages).strip()
        if not output:
            raise CliAgentError("External CLI agent returned empty output")
        return output


def _matches_turn(
    *,
    params: dict[str, JsonValue],
    thread_id: str,
    turn_id: str,
) -> bool:
    if _as_str(params.get("threadId")) != thread_id:
        return False
    direct_turn_id = _as_str(params.get("turnId"))
    if direct_turn_id is not None:
        return direct_turn_id == turn_id
    turn = _as_object(params.get("turn"))
    return _as_str(turn.get("id")) == turn_id


def _turn_status(params: dict[str, JsonValue]) -> str | None:
    turn = _as_object(params.get("turn"))
    return _as_str(turn.get("status"))


def _turn_error_message(params: dict[str, JsonValue]) -> str | None:
    error = _as_object(params.get("error"))
    if not error:
        turn = _as_object(params.get("turn"))
        error = _as_object(turn.get("error"))
    message = _as_str(error.get("message"))
    details = _as_str(error.get("additionalDetails"))
    if message is not None and details is not None:
        return f"{message}: {details}"
    return message or details


class _StdioCliJsonRpcClient:
    def __init__(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        runtime_cwd: Path | None,
        transport: StdioTransportConfig,
    ) -> None:
        self._command = command
        self._args = args
        self._runtime_cwd = runtime_cwd
        self._transport = transport
        self._process: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._next_request_id = 0
        self._pending: dict[JsonRpcId, asyncio.Future[dict[str, JsonValue]]] = {}
        self._notifications: asyncio.Queue[_CliJsonRpcNotification] = asyncio.Queue()

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        env = os.environ.copy()
        for item in self._transport.env:
            if item.value is not None:
                env[item.name] = item.value
        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
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
            if request_id in self._pending:
                del self._pending[request_id]

    async def send_notification(
        self,
        method: str,
        params: dict[str, JsonValue] | None = None,
    ) -> None:
        await self.start()
        message: dict[str, JsonValue] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params
        await self._send_raw(message)

    async def next_notification(self) -> _CliJsonRpcNotification:
        return await self._notifications.get()

    async def close(self) -> None:
        self._fail_pending(CliAgentError("CLI JSON-RPC runtime closed"))
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
            raise CliAgentError("CLI JSON-RPC runtime is not started")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        async with self._write_lock:
            self._process.stdin.write(payload.encode("utf-8") + b"\n")
            await self._process.stdin.drain()

    async def _read_stdout_loop(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        try:
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
                await self._handle_payload(
                    {str(key): value for key, value in payload.items()}
                )
        except Exception as exc:
            self._fail_pending(exc)
            return
        self._fail_pending(
            CliAgentError("CLI JSON-RPC runtime closed stdout before responding")
        )

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
                event="external_agent.cli_jsonrpc.stderr",
                message="External CLI JSON-RPC runtime wrote to stderr",
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
                    CliJsonRpcError(
                        code=_as_int(error_payload.get("code")) or -32000,
                        message=_as_str(error_payload.get("message"))
                        or "CLI JSON-RPC request failed",
                    )
                )
                return
            future.set_result(_as_object(payload.get("result")))
            return

        method = _as_str(payload.get("method"))
        if method is None:
            return
        params = _as_object(payload.get("params"))
        if response_id is None:
            await self._notifications.put(
                _CliJsonRpcNotification(method=method, params=params)
            )
            return
        await self._send_raw(
            {
                "jsonrpc": "2.0",
                "id": response_id,
                "error": {
                    "code": -32601,
                    "message": (
                        "Relay Teams CLI runtime does not handle server request "
                        f"{method}"
                    ),
                },
            }
        )

    def _fail_pending(self, exc: BaseException) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(exc)


async def _read_next_stdio_message(stream: asyncio.StreamReader) -> bytes | None:
    first_line = await stream.readline()
    if not first_line:
        return None
    if first_line.startswith(b"Content-Length:"):
        try:
            content_length = int(first_line.partition(b":")[2].strip())
        except ValueError as exc:
            raise CliAgentError(
                "Invalid Content-Length header from CLI runtime"
            ) from exc
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


def _as_text(value: JsonValue | None) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _as_int(value: JsonValue | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
