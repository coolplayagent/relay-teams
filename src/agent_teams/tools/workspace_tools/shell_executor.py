# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import AsyncGenerator

from agent_teams.env import get_env_var
from agent_teams.tools.workspace_tools.shell_policy import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
)


def resolve_bash_path() -> str:
    """Resolve the bash executable path for shell commands."""
    env_path = get_env_var("GIT_BASH_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    which_bash = shutil.which("bash")
    if which_bash:
        return which_bash

    candidates = (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    )
    for item in candidates:
        if Path(item).exists():
            return item

    raise FileNotFoundError("Git Bash executable not found; set GIT_BASH_PATH")


def normalize_timeout(timeout_ms: int | None) -> int:
    """Normalize timeout in milliseconds and apply policy limits."""
    if timeout_ms is None:
        return DEFAULT_TIMEOUT_SECONDS * 1000

    if timeout_ms < 1:
        raise ValueError("timeout_ms must be >= 1")

    max_ms = MAX_TIMEOUT_SECONDS * 1000
    if timeout_ms > max_ms:
        return max_ms

    return timeout_ms


COMMAND_PATH_PATTERNS = [
    (r"^cd\s+(.+?)(?:\s|$)", "cd"),
    (r"^rm\s+-+\s*(.+?)(?:\s|$)", "rm"),
    (r"^cp\s+(.+?)(?:\s|$)", "cp"),
    (r"^mv\s+(.+?)(?:\s|$)", "mv"),
    (r"^mkdir\s+-+\s*(.+?)(?:\s|$)", "mkdir"),
    (r"^touch\s+(.+?)(?:\s|$)", "touch"),
    (r"^chmod\s+(.+?)(?:\s|$)", "chmod"),
    (r"^chown\s+(.+?)(?:\s|$)", "chown"),
    (r"^cat\s+(.+?)(?:\s|$)", "cat"),
    (r"^ls\s+(.+?)(?:\s|$)", "ls"),
    (r"^find\s+(.+?)(?:\s|$)", "find"),
]


def extract_paths_from_command(command: str) -> list[str]:
    """Extract candidate path arguments from shell commands."""
    paths: list[str] = []
    lines = command.split("\n")

    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            continue

        parts = shlex.split(stripped_line)
        if not parts:
            continue

        cmd = parts[0]

        if cmd in ("cd", "ls", "cat"):
            if len(parts) > 1:
                path = parts[1]
                if not path.startswith("-"):
                    paths.append(path)
        elif cmd in ("rm", "cp", "mv", "touch", "chmod", "chown", "find"):
            for part in parts[1:]:
                if not part.startswith("-"):
                    paths.append(part)
                    break
        elif cmd == "mkdir":
            for part in parts[1:]:
                if part.startswith("-"):
                    continue
                paths.append(part)
                break

    return paths


async def spawn_shell(
    command: str,
    cwd: Path,
    timeout_ms: int = 30000,
    env: dict[str, str] | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """Run shell command with streaming stdout/stderr chunks."""
    bash = resolve_bash_path()

    shell_env = os.environ.copy()
    if env:
        shell_env.update(env)

    proc = await asyncio.create_subprocess_exec(
        bash,
        "-lc",
        command,
        cwd=str(cwd),
        env=shell_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout = proc.stdout
    stderr = proc.stderr
    if stdout is None or stderr is None:
        raise RuntimeError("Failed to capture subprocess streams")

    queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

    async def _pump(stream_name: str, stream: asyncio.StreamReader) -> None:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            await queue.put((stream_name, chunk.decode("utf-8", errors="replace")))
        await queue.put(None)

    stdout_task = asyncio.create_task(_pump("stdout", stdout))
    stderr_task = asyncio.create_task(_pump("stderr", stderr))
    timeout_seconds = max(0.001, timeout_ms / 1000.0)
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    stream_eof = 0

    try:
        while True:
            if stream_eof >= 2 and proc.returncode is not None:
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                item = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise asyncio.TimeoutError from exc
            if item is None:
                stream_eof += 1
                continue
            yield item
    finally:
        for task in (stdout_task, stderr_task):
            if not task.done():
                task.cancel()
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


def run_git_bash(
    *,
    command: str,
    workdir: Path,
    timeout_seconds: int,
) -> tuple[int, str, str, bool]:
    """Run command synchronously under bash for compatibility."""
    bash = resolve_bash_path()
    try:
        proc = subprocess.run(
            [bash, "-lc", command],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        return 124, str(out), str(err), True
