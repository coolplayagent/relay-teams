# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    ExternalAgentTestResult,
    StdioTransportConfig,
)


class CliAgentError(RuntimeError):
    pass


async def probe_cli_agent(config: ExternalAgentConfig) -> ExternalAgentTestResult:
    try:
        transport = _stdio_transport(config)
        command = str(transport.command)
        resolved = shutil.which(command)
        if resolved is None and not Path(command).exists():
            raise CliAgentError(f"CLI command not found: {command}")
        return ExternalAgentTestResult(
            ok=True,
            message="External CLI agent command is available.",
            protocol=ExternalAgentProtocol.CLI,
            agent_name=Path(command).name,
        )
    except Exception as exc:
        return ExternalAgentTestResult(
            ok=False,
            message=str(exc) or exc.__class__.__name__,
            protocol=ExternalAgentProtocol.CLI,
        )


async def run_cli_agent_prompt(
    *,
    config: ExternalAgentConfig,
    prompt: str,
    runtime_cwd: Path,
    timeout_seconds: float,
) -> str:
    transport = _stdio_transport(config)
    command, args, output_path = _build_command(
        transport=transport,
        runtime_cwd=runtime_cwd,
    )
    env = os.environ.copy()
    for item in transport.env:
        if item.value is not None:
            env[item.name] = item.value
    process = await asyncio.create_subprocess_exec(
        command,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=runtime_cwd,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(prompt.encode("utf-8")),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise CliAgentError(
            f"External CLI agent timed out after {timeout_seconds:g} seconds"
        ) from exc
    output = (
        _read_output_file(output_path)
        or stdout.decode(
            "utf-8",
            errors="replace",
        ).strip()
    )
    if process.returncode != 0:
        error_text = stderr.decode("utf-8", errors="replace").strip()
        raise CliAgentError(
            error_text
            or output
            or f"External CLI agent exited with {process.returncode}"
        )
    if not output:
        error_text = stderr.decode("utf-8", errors="replace").strip()
        raise CliAgentError(error_text or "External CLI agent returned empty output")
    return output


def _stdio_transport(config: ExternalAgentConfig) -> StdioTransportConfig:
    if not isinstance(config.transport, StdioTransportConfig):
        raise CliAgentError("CLI agent runtimes require stdio transport")
    return config.transport


def _build_command(
    *,
    transport: StdioTransportConfig,
    runtime_cwd: Path,
) -> tuple[str, tuple[str, ...], Path | None]:
    command = transport.command
    args = tuple(_normalize_codex_yolo_arg(arg) for arg in transport.args)
    if _is_codex_command(command):
        return _build_codex_exec_command(
            command=command,
            args=args,
            runtime_cwd=runtime_cwd,
        )
    return command, args, None


def _build_codex_exec_command(
    *,
    command: str,
    args: tuple[str, ...],
    runtime_cwd: Path,
) -> tuple[str, tuple[str, ...], Path]:
    next_args = args
    if not _has_codex_exec_subcommand(next_args):
        next_args = ("exec", *next_args)
    if not _has_codex_cwd(next_args):
        next_args = (*next_args, "--cd", str(runtime_cwd))
    if "--color" not in next_args and not any(
        arg.startswith("--color=") for arg in next_args
    ):
        next_args = (*next_args, "--color", "never")
    output_fd, output_name = tempfile.mkstemp(
        prefix="relay-teams-codex-",
        suffix=".txt",
    )
    os.close(output_fd)
    output_file = Path(output_name)
    if "--output-last-message" not in next_args and not any(
        arg.startswith("--output-last-message=") for arg in next_args
    ):
        next_args = (*next_args, "--output-last-message", str(output_file))
    return command, next_args, output_file


def _read_output_file(output_path: Path | None) -> str:
    if output_path is None:
        return ""
    try:
        text = output_path.read_text(encoding="utf-8").strip()
    finally:
        output_path.unlink(missing_ok=True)
    return text


def _normalize_codex_yolo_arg(arg: str) -> str:
    if arg == "--yolo":
        return "--dangerously-bypass-approvals-and-sandbox"
    return arg


def _is_codex_command(command: str) -> bool:
    name = Path(command).name.lower()
    return name == "codex" or name.startswith("codex-")


def _has_codex_exec_subcommand(args: tuple[str, ...]) -> bool:
    return any(arg in {"exec", "e"} for arg in args)


def _has_codex_cwd(args: tuple[str, ...]) -> bool:
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in {"-C", "--cd"}:
            return True
        if arg.startswith("--cd="):
            return True
        if arg in {"-c", "--config", "-m", "--model", "-p", "--profile"}:
            skip_next = True
    return False
