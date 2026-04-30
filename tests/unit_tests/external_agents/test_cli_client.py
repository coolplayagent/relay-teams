# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from relay_teams.external_agents.cli_client import (
    _build_command,
    probe_cli_agent,
    run_cli_agent_prompt,
)
from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    StdioTransportConfig,
)


def _build_cli_agent(command: str, args: tuple[str, ...]) -> ExternalAgentConfig:
    return ExternalAgentConfig(
        agent_id="cli_agent",
        name="CLI Agent",
        protocol=ExternalAgentProtocol.CLI,
        transport=StdioTransportConfig(command=command, args=args),
    )


@pytest.mark.asyncio
async def test_probe_cli_agent_checks_command_availability() -> None:
    result = await probe_cli_agent(_build_cli_agent(sys.executable, ()))

    assert result.ok is True
    assert result.protocol == ExternalAgentProtocol.CLI


@pytest.mark.asyncio
async def test_run_cli_agent_prompt_passes_prompt_to_stdin(tmp_path: Path) -> None:
    result = await run_cli_agent_prompt(
        config=_build_cli_agent(
            sys.executable,
            (
                "-c",
                "import sys; print(sys.stdin.read().strip().upper())",
            ),
        ),
        prompt="hello runtime",
        runtime_cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result == "HELLO RUNTIME"


def test_codex_yolo_command_is_normalized_for_exec_runtime(tmp_path: Path) -> None:
    command, args, output_path = _build_command(
        transport=StdioTransportConfig(command="codex", args=("--yolo",)),
        runtime_cwd=tmp_path,
    )

    assert command == "codex"
    assert args[0] == "exec"
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert "--cd" in args
    assert str(tmp_path) in args
    assert "--output-last-message" in args
    assert output_path is not None
    output_path.unlink(missing_ok=True)
