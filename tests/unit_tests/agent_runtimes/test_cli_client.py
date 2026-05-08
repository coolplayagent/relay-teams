# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from relay_teams.agent_runtimes.clients import cli as cli_client_module
from relay_teams.agent_runtimes.clients.cli import (
    CliAgentError,
    _CliJsonRpcNotification,
    _StdioCliJsonRpcClient,
    _build_command_args,
    _cli_command_exists,
    _read_next_stdio_message,
    _start_cli_thread,
    _start_cli_turn,
    _stdio_transport,
    _wait_for_cli_turn_output,
    probe_cli_agent,
    run_cli_agent_prompt,
)
from relay_teams.agent_runtimes.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    ExternalAgentSecretBinding,
    StdioTransportConfig,
    StreamableHttpTransportConfig,
)

_JSON_RPC_RUNTIME_SCRIPT = r"""
import json
import sys

thread_id = "thread-1"
turn_id = "turn-1"

for raw_line in sys.stdin:
    if not raw_line.strip():
        continue
    message = json.loads(raw_line)
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        print(
            json.dumps(
                {
                    "id": message_id,
                    "result": {
                        "userAgent": "fake-cli-runtime/1.0",
                        "codexHome": "/tmp/codex",
                        "platformFamily": "unix",
                        "platformOs": "linux",
                    },
                }
            ),
            flush=True,
        )
    elif method == "initialized":
        continue
    elif method == "thread/start":
        print(
            json.dumps({"id": message_id, "result": {"thread": {"id": thread_id}}}),
            flush=True,
        )
    elif method == "turn/start":
        params = message["params"]
        assert params["cwd"]
        assert params["input"][0]["type"] == "text"
        assert "hello runtime" in params["input"][0]["text"]
        print(
            json.dumps(
                {
                    "id": message_id,
                    "result": {
                        "turn": {"id": turn_id, "status": "inProgress", "items": []}
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": "item-1",
                        "delta": "JSON RPC ",
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": "item-1",
                        "delta": "output.",
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "completed", "items": []},
                    },
                }
            ),
            flush=True,
        )
    else:
        print(
            json.dumps(
                {
                    "id": message_id,
                    "error": {"code": -32601, "message": f"unknown method {method}"},
                }
            ),
            flush=True,
        )
"""

_JSON_RPC_ITEM_COMPLETED_RUNTIME_SCRIPT = r"""
import json
import sys

thread_id = "thread-1"
turn_id = "turn-1"

for raw_line in sys.stdin:
    if not raw_line.strip():
        continue
    message = json.loads(raw_line)
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        print(json.dumps({"id": message_id, "result": {"userAgent": "fake/1"}}), flush=True)
    elif method == "initialized":
        continue
    elif method == "thread/start":
        print(json.dumps({"id": message_id, "result": {"thread": {"id": thread_id}}}), flush=True)
    elif method == "turn/start":
        print(
            json.dumps(
                {
                    "id": message_id,
                    "result": {
                        "turn": {"id": turn_id, "status": "inProgress", "items": []}
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "id": "item-1",
                            "type": "agentMessage",
                            "text": "completed item output",
                        },
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "completed", "items": []},
                    },
                }
            ),
            flush=True,
        )
"""

_JSON_RPC_CLOSE_DURING_TURN_SCRIPT = r"""
import json
import sys

thread_id = "thread-1"
turn_id = "turn-1"

for raw_line in sys.stdin:
    if not raw_line.strip():
        continue
    message = json.loads(raw_line)
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        print(json.dumps({"id": message_id, "result": {"userAgent": "fake/1"}}), flush=True)
    elif method == "initialized":
        continue
    elif method == "thread/start":
        print(json.dumps({"id": message_id, "result": {"thread": {"id": thread_id}}}), flush=True)
    elif method == "turn/start":
        print(
            json.dumps(
                {
                    "id": message_id,
                    "result": {
                        "turn": {"id": turn_id, "status": "inProgress", "items": []}
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": "item-1",
                        "delta": "partial output",
                    },
                }
            ),
            flush=True,
        )
        raise SystemExit(0)
"""


def _build_cli_agent(
    command: str,
    args: tuple[str, ...],
    *,
    env: tuple[ExternalAgentSecretBinding, ...] = (),
) -> ExternalAgentConfig:
    return ExternalAgentConfig(
        agent_id="cli_agent",
        name="CLI Agent",
        protocol=ExternalAgentProtocol.CLI,
        transport=StdioTransportConfig(command=command, args=args, env=env),
    )


class _NotificationClient:
    def __init__(self, notifications: list[_CliJsonRpcNotification]) -> None:
        self._notifications = notifications

    async def next_notification(self) -> _CliJsonRpcNotification:
        return self._notifications.pop(0)


class _RequestClient:
    def __init__(self, response: dict[str, JsonValue]) -> None:
        self.response = response
        self.method: str | None = None
        self.params: dict[str, JsonValue] | None = None

    async def send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        self.method = method
        self.params = params
        return self.response


class _TerminateTimeoutProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise asyncio.TimeoutError
        return self.returncode or 0


@pytest.mark.asyncio
async def test_probe_cli_agent_initializes_stdio_json_rpc_runtime() -> None:
    result = await probe_cli_agent(
        _build_cli_agent(sys.executable, ("-c", _JSON_RPC_RUNTIME_SCRIPT))
    )

    assert result.ok is True
    assert result.protocol == ExternalAgentProtocol.CLI
    assert result.protocol_version_text == "stdio-jsonrpc"
    assert result.agent_name == Path(sys.executable).name
    assert result.agent_version == "fake-cli-runtime/1.0"


@pytest.mark.asyncio
async def test_probe_cli_agent_reports_missing_command(tmp_path: Path) -> None:
    result = await probe_cli_agent(_build_cli_agent(str(tmp_path / "missing"), ()))

    assert result.ok is False
    assert "CLI command not found" in result.message


@pytest.mark.asyncio
async def test_probe_cli_agent_resolves_relative_command_from_runtime_cwd(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "runtime-agent"
    executable.write_text(
        f"#!{sys.executable}\n{_JSON_RPC_RUNTIME_SCRIPT}",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = await probe_cli_agent(
        _build_cli_agent("./bin/runtime-agent", ()),
        runtime_cwd=tmp_path,
    )

    assert result.ok is True
    assert result.agent_name == "runtime-agent"


@pytest.mark.asyncio
async def test_probe_cli_agent_uses_transport_env_for_command_lookup(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "runtime-agent"
    executable.write_text(
        f"#!{sys.executable}\n{_JSON_RPC_RUNTIME_SCRIPT}",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    result = await probe_cli_agent(
        _build_cli_agent(
            "runtime-agent",
            (),
            env=(ExternalAgentSecretBinding(name="PATH", value=str(tmp_path)),),
        )
    )

    assert result.ok is True
    assert result.agent_name == "runtime-agent"


@pytest.mark.asyncio
async def test_run_cli_agent_prompt_uses_thread_turn_json_rpc(tmp_path: Path) -> None:
    result = await run_cli_agent_prompt(
        config=_build_cli_agent(sys.executable, ("-c", _JSON_RPC_RUNTIME_SCRIPT)),
        prompt="hello runtime",
        runtime_cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result == "JSON RPC output."


@pytest.mark.asyncio
async def test_run_cli_agent_prompt_uses_completed_item_fallback(
    tmp_path: Path,
) -> None:
    result = await run_cli_agent_prompt(
        config=_build_cli_agent(
            sys.executable,
            ("-c", _JSON_RPC_ITEM_COMPLETED_RUNTIME_SCRIPT),
        ),
        prompt="hello runtime",
        runtime_cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result == "completed item output"


@pytest.mark.asyncio
async def test_run_cli_agent_prompt_raises_when_runtime_closes_during_turn(
    tmp_path: Path,
) -> None:
    with pytest.raises(CliAgentError, match="closed stdout"):
        await run_cli_agent_prompt(
            config=_build_cli_agent(
                sys.executable,
                ("-c", _JSON_RPC_CLOSE_DURING_TURN_SCRIPT),
            ),
            prompt="hello runtime",
            runtime_cwd=tmp_path,
            timeout_seconds=5,
        )


@pytest.mark.asyncio
async def test_run_cli_agent_prompt_uses_shared_timeout_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_timeouts: list[tuple[str, float]] = []

    async def fake_initialize_cli_runtime(
        *,
        client: _StdioCliJsonRpcClient,
        timeout_seconds: float,
    ) -> dict[str, JsonValue]:
        observed_timeouts.append(("initialize", timeout_seconds))
        await asyncio.sleep(0.02)
        return {}

    async def fake_start_cli_thread(
        *,
        client: _StdioCliJsonRpcClient,
        runtime_cwd: Path,
        timeout_seconds: float,
    ) -> str:
        observed_timeouts.append(("thread/start", timeout_seconds))
        await asyncio.sleep(0.02)
        return "thread-1"

    async def fake_start_cli_turn(
        *,
        client: _StdioCliJsonRpcClient,
        prompt: str,
        runtime_cwd: Path,
        thread_id: str,
        timeout_seconds: float,
    ) -> str:
        observed_timeouts.append(("turn/start", timeout_seconds))
        await asyncio.sleep(0.02)
        return "turn-1"

    async def fake_wait_for_cli_turn_output(
        *,
        client: _StdioCliJsonRpcClient,
        thread_id: str,
        turn_id: str,
        timeout_seconds: float,
    ) -> str:
        observed_timeouts.append(("turn/wait", timeout_seconds))
        return "done"

    monkeypatch.setattr(
        "relay_teams.agent_runtimes.clients.cli._initialize_cli_runtime",
        fake_initialize_cli_runtime,
    )
    monkeypatch.setattr(
        "relay_teams.agent_runtimes.clients.cli._start_cli_thread",
        fake_start_cli_thread,
    )
    monkeypatch.setattr(
        "relay_teams.agent_runtimes.clients.cli._start_cli_turn",
        fake_start_cli_turn,
    )
    monkeypatch.setattr(
        "relay_teams.agent_runtimes.clients.cli._wait_for_cli_turn_output",
        fake_wait_for_cli_turn_output,
    )

    result = await run_cli_agent_prompt(
        config=_build_cli_agent(sys.executable, ("-c", "")),
        prompt="hello runtime",
        runtime_cwd=tmp_path,
        timeout_seconds=30,
    )

    assert result == "done"
    assert [name for name, _timeout in observed_timeouts] == [
        "initialize",
        "thread/start",
        "turn/start",
        "turn/wait",
    ]
    timeout_values = [timeout for _name, timeout in observed_timeouts]
    assert all(0 < timeout <= 30 for timeout in timeout_values)
    assert timeout_values[0] > 10
    assert timeout_values == sorted(timeout_values, reverse=True)
    assert len(set(timeout_values)) == len(timeout_values)


@pytest.mark.asyncio
async def test_stdio_cli_client_close_waits_after_kill() -> None:
    process = _TerminateTimeoutProcess()
    client = _StdioCliJsonRpcClient(
        command=sys.executable,
        args=("-c", ""),
        runtime_cwd=None,
        transport=StdioTransportConfig(command=sys.executable),
    )
    client._process = cast(asyncio.subprocess.Process, process)

    await client.close()

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_calls == 2


def test_stdio_transport_rejects_non_stdio_config() -> None:
    config = ExternalAgentConfig(
        agent_id="cli_agent",
        name="CLI Agent",
        protocol=ExternalAgentProtocol.ACP,
        transport=StreamableHttpTransportConfig(url="http://127.0.0.1:8000/rpc"),
    )

    with pytest.raises(CliAgentError, match="stdio transport"):
        _stdio_transport(config)


def test_cli_command_exists_checks_direct_paths_and_path_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "runtime-agent"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    non_executable = tmp_path / "not-executable"
    non_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable_directory = tmp_path / "runtime-directory"
    executable_directory.mkdir()
    executable_directory.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _cli_command_exists(str(executable)) is True
    assert _cli_command_exists("runtime-agent") is True
    if os.name != "nt":
        assert _cli_command_exists(str(non_executable)) is False
        assert _cli_command_exists("not-executable") is False
    assert _cli_command_exists(str(executable_directory)) is False
    assert _cli_command_exists("missing-agent") is False
    assert _cli_command_exists("./runtime-agent", runtime_cwd=tmp_path) is True
    assert (
        _cli_command_exists(
            "runtime-agent",
            env={"PATH": str(tmp_path)},
        )
        is True
    )


def test_cli_command_exists_checks_windows_pathext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_paths: list[Path] = []

    def fake_executable_path_exists(path: Path) -> bool:
        checked_paths.append(path)
        return path.name == "runtime-agent.EXE"

    monkeypatch.setattr(
        cli_client_module,
        "_executable_path_exists",
        fake_executable_path_exists,
    )
    monkeypatch.setattr(cli_client_module, "Path", type(tmp_path))
    monkeypatch.setattr(cli_client_module.os, "name", "nt")

    assert (
        _cli_command_exists(
            "runtime-agent",
            env={"PATH": str(tmp_path), "PATHEXT": ".EXE;.BAT"},
        )
        is True
    )
    assert tmp_path / "runtime-agent.EXE" in checked_paths


def test_codex_command_uses_app_server_stdio_runtime() -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(command="codex", args=()),
    )

    assert args == ("app-server", "--listen", "stdio://")


@pytest.mark.parametrize(
    "command",
    (
        "codex.exe",
        "codex.cmd",
        "codex-agent.bat",
        "C:\\Tools\\codex.ps1",
    ),
)
def test_codex_platform_executable_names_use_app_server(command: str) -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(command=command, args=()),
    )

    assert args == ("app-server", "--listen", "stdio://")


def test_codex_app_server_command_keeps_existing_subcommand_args() -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(
            command="codex",
            args=("app-server", "--config", "model='gpt-5.5'"),
        ),
    )

    assert args == (
        "app-server",
        "--config",
        "model='gpt-5.5'",
        "--listen",
        "stdio://",
    )


def test_codex_global_app_server_options_are_preserved_during_migration() -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(
            command="codex",
            args=(
                "-c",
                "model='gpt-5.5'",
                "--config",
                "provider='openai'",
                "--disable",
                "telemetry",
                "--analytics-default-enabled",
            ),
        ),
    )

    assert args == (
        "app-server",
        "--listen",
        "stdio://",
        "-c",
        "model='gpt-5.5'",
        "--config",
        "provider='openai'",
        "--disable",
        "telemetry",
        "--analytics-default-enabled",
    )


def test_codex_legacy_exec_args_are_not_forwarded_to_app_server() -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(
            command="codex",
            args=(
                "--model",
                "exec",
                "exec",
                "--yolo",
                "--output-last-message",
                "message.txt",
            ),
        ),
    )

    assert args == ("app-server", "--listen", "stdio://")


def test_codex_app_server_listener_is_not_duplicated() -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(
            command="codex",
            args=("app-server", "--listen", "stdio://"),
        ),
    )

    assert args == ("app-server", "--listen", "stdio://")


@pytest.mark.parametrize(
    "args",
    (
        ("app-server", "--listen", "ws://127.0.0.1:0"),
        ("app-server", "--listen=ws://127.0.0.1:0"),
        ("app-server", "--listen", "off"),
    ),
)
def test_codex_app_server_listener_is_forced_to_stdio(args: tuple[str, ...]) -> None:
    built_args = _build_command_args(
        transport=StdioTransportConfig(command="codex", args=args),
    )

    assert built_args == ("app-server", "--listen", "stdio://")


@pytest.mark.parametrize(
    "args",
    (
        ("--listen", "ws://127.0.0.1:0"),
        ("--listen=ws://127.0.0.1:0",),
        ("--listen", "off"),
    ),
)
def test_bare_codex_listener_args_are_replaced_with_stdio(
    args: tuple[str, ...],
) -> None:
    built_args = _build_command_args(
        transport=StdioTransportConfig(command="codex", args=args),
    )

    assert built_args == ("app-server", "--listen", "stdio://")


def test_non_codex_yolo_argument_is_preserved() -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(command="custom-agent", args=("--yolo",)),
    )

    assert args == ("--yolo",)


@pytest.mark.asyncio
async def test_start_cli_thread_requires_thread_id(tmp_path: Path) -> None:
    client = _RequestClient({"thread": {}})

    with pytest.raises(CliAgentError, match="thread id"):
        await _start_cli_thread(
            client=cast(_StdioCliJsonRpcClient, client),
            runtime_cwd=tmp_path,
            timeout_seconds=1,
        )

    assert client.method == "thread/start"


@pytest.mark.asyncio
async def test_start_cli_turn_requires_turn_id(tmp_path: Path) -> None:
    client = _RequestClient({"turn": {}})

    with pytest.raises(CliAgentError, match="turn id"):
        await _start_cli_turn(
            client=cast(_StdioCliJsonRpcClient, client),
            prompt="hello",
            runtime_cwd=tmp_path,
            thread_id="thread-1",
            timeout_seconds=1,
        )

    assert client.method == "turn/start"


@pytest.mark.asyncio
async def test_wait_for_cli_turn_output_ignores_other_turns() -> None:
    client = _NotificationClient(
        [
            _CliJsonRpcNotification(
                method="item/agentMessage/delta",
                params={"threadId": "other", "turnId": "turn-1", "delta": "wrong"},
            ),
            _CliJsonRpcNotification(
                method="item/agentMessage/delta",
                params={"threadId": "thread-1", "turnId": "turn-1", "delta": "right"},
            ),
            _CliJsonRpcNotification(
                method="turn/completed",
                params={
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            ),
        ]
    )

    result = await _wait_for_cli_turn_output(
        client=cast(_StdioCliJsonRpcClient, client),
        thread_id="thread-1",
        turn_id="turn-1",
        timeout_seconds=1,
    )

    assert result == "right"


@pytest.mark.asyncio
async def test_wait_for_cli_turn_output_raises_failed_turn_error() -> None:
    client = _NotificationClient(
        [
            _CliJsonRpcNotification(
                method="error",
                params={
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "error": {"message": "runtime failed", "additionalDetails": "bad"},
                    "willRetry": False,
                },
            ),
            _CliJsonRpcNotification(
                method="turn/completed",
                params={
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "failed"},
                },
            ),
        ]
    )

    with pytest.raises(CliAgentError, match="runtime failed: bad"):
        await _wait_for_cli_turn_output(
            client=cast(_StdioCliJsonRpcClient, client),
            thread_id="thread-1",
            turn_id="turn-1",
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_wait_for_cli_turn_output_raises_interrupted_turn_error() -> None:
    client = _NotificationClient(
        [
            _CliJsonRpcNotification(
                method="item/agentMessage/delta",
                params={
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "delta": "partial",
                },
            ),
            _CliJsonRpcNotification(
                method="turn/completed",
                params={
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "interrupted"},
                },
            ),
        ]
    )

    with pytest.raises(CliAgentError, match="turn interrupted"):
        await _wait_for_cli_turn_output(
            client=cast(_StdioCliJsonRpcClient, client),
            thread_id="thread-1",
            turn_id="turn-1",
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_wait_for_cli_turn_output_rejects_empty_output() -> None:
    client = _NotificationClient(
        [
            _CliJsonRpcNotification(
                method="turn/completed",
                params={
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            )
        ]
    )

    with pytest.raises(CliAgentError, match="empty output"):
        await _wait_for_cli_turn_output(
            client=cast(_StdioCliJsonRpcClient, client),
            thread_id="thread-1",
            turn_id="turn-1",
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_read_next_stdio_message_supports_content_length() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"Content-Length: 5\r\n\r\nhello")
    reader.feed_eof()

    assert await _read_next_stdio_message(reader) == b"hello"


@pytest.mark.asyncio
async def test_read_next_stdio_message_rejects_invalid_content_length() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"Content-Length: nope\r\n\r\n")
    reader.feed_eof()

    with pytest.raises(CliAgentError, match="Invalid Content-Length"):
        await _read_next_stdio_message(reader)
