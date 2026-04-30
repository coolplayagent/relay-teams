# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from relay_teams.external_agents.cli_client import (
    _build_command_args,
    probe_cli_agent,
    run_cli_agent_prompt,
)
from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    StdioTransportConfig,
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


def _build_cli_agent(command: str, args: tuple[str, ...]) -> ExternalAgentConfig:
    return ExternalAgentConfig(
        agent_id="cli_agent",
        name="CLI Agent",
        protocol=ExternalAgentProtocol.CLI,
        transport=StdioTransportConfig(command=command, args=args),
    )


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


def test_codex_command_uses_app_server_stdio_runtime() -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(command="codex", args=()),
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


def test_non_codex_yolo_argument_is_preserved() -> None:
    args = _build_command_args(
        transport=StdioTransportConfig(command="custom-agent", args=("--yolo",)),
    )

    assert args == ("--yolo",)
