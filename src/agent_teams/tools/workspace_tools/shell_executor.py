# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import signal
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import AsyncGenerator

from agent_teams.env import build_github_cli_env, build_subprocess_env, get_env_var
from agent_teams.env.github_config_service import GitHubConfigService
from agent_teams.env.runtime_env import get_app_config_dir
from agent_teams.tools.workspace_tools.github_cli import get_gh_path
from agent_teams.tools.workspace_tools.shell_policy import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
)

WINDOWS_GIT_BASH_CANDIDATES = (
    Path(r"C:\Program Files\Git\bin\bash.exe"),
    Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
    Path(r"C:\Program Files (x86)\Git\usr\bin\bash.exe"),
)
_BASH_STARTUP_ENV_KEYS = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "PROMPT_COMMAND",
        "PS0",
        "PS1",
        "PS2",
        "PS4",
    }
)
_BASH_STARTUP_ENV_PREFIXES = ("BASH_FUNC_",)


def resolve_bash_path() -> str:
    """Resolve the bash executable path for shell commands."""
    env_path = get_env_var("GIT_BASH_PATH")
    if env_path:
        resolved_env_path = Path(env_path).expanduser()
        if resolved_env_path.is_file():
            return str(resolved_env_path)

    if _is_windows():
        return _resolve_windows_bash_path()
    return _resolve_posix_bash_path()


def _is_windows() -> bool:
    return os.name == "nt"


def _resolve_windows_bash_path() -> str:
    for candidate in _iter_windows_git_bash_candidates():
        if candidate.is_file():
            return str(candidate)

    which_bash = shutil.which("bash")
    if which_bash:
        bash_path = Path(which_bash)
        if bash_path.is_file() and not _is_wsl_bash_launcher(bash_path):
            return str(bash_path)

    raise FileNotFoundError(
        "Git Bash executable not found on Windows; install Git for Windows or set GIT_BASH_PATH"
    )


def _resolve_posix_bash_path() -> str:
    which_bash = shutil.which("bash")
    if which_bash:
        return which_bash
    raise FileNotFoundError("bash executable not found")


def _iter_windows_git_bash_candidates() -> tuple[Path, ...]:
    candidates = list(WINDOWS_GIT_BASH_CANDIDATES)
    git_path = shutil.which("git")
    if git_path:
        git_root = Path(git_path).parent.parent
        candidates.extend(
            (
                git_root / "bin" / "bash.exe",
                git_root / "usr" / "bin" / "bash.exe",
            )
        )

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate).lower()
        if normalized in seen:
            continue
        deduped.append(candidate)
        seen.add(normalized)
    return tuple(deduped)


def _is_wsl_bash_launcher(path: Path) -> bool:
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    launcher_paths = {
        (windows_dir / "System32" / "bash.exe").resolve(),
        (windows_dir / "Sysnative" / "bash.exe").resolve(),
    }
    return path.resolve() in launcher_paths


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


_SIGKILL_GRACE_SECONDS = 5


def _creation_flags() -> int:
    """Return Windows process-creation flags (0 on non-Windows)."""
    if _is_windows():
        return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return 0


def _start_new_session() -> bool:
    """Return True on non-Windows to create a new process group."""
    return not _is_windows()


def _sanitize_bash_env(env: dict[str, str]) -> dict[str, str]:
    sanitized = dict(env)
    for key in tuple(sanitized.keys()):
        if key in _BASH_STARTUP_ENV_KEYS:
            sanitized.pop(key, None)
            continue
        if any(key.startswith(prefix) for prefix in _BASH_STARTUP_ENV_PREFIXES):
            sanitized.pop(key, None)
    return sanitized


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate the process and its entire process tree."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    if pid is None:
        return

    if _is_windows():
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/f",
                "/t",
                "/pid",
                str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=_SIGKILL_GRACE_SECONDS)
        except (OSError, asyncio.TimeoutError):
            proc.kill()
        await proc.wait()
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=_SIGKILL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


async def spawn_shell(
    command: str,
    cwd: Path,
    timeout_ms: int = 30000,
    env: dict[str, str] | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """Run shell command with streaming stdout/stderr chunks.

    Yields ``("stdout", data)`` and ``("stderr", data)`` tuples as output
    arrives, followed by a final ``("exit_code", "<code>")`` sentinel once the
    process exits.
    """
    proc = await create_shell_subprocess(
        command=command,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
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
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            if stream_eof >= 2:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise asyncio.TimeoutError from exc
                yield ("exit_code", str(proc.returncode or 0))
                break
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
        await _kill_process_tree(proc)


def run_git_bash(
    *,
    command: str,
    workdir: Path,
    timeout_seconds: int,
) -> tuple[int, str, str, bool]:
    """Run command synchronously under bash for compatibility."""
    bash = resolve_bash_path()
    shell_env = build_shell_env_sync()
    try:
        proc = subprocess.run(
            [bash, "-lc", command],
            cwd=str(workdir),
            env=shell_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            start_new_session=_start_new_session(),
            creationflags=_creation_flags(),
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        return 124, str(out), str(err), True


def _load_github_cli_env() -> dict[str, str]:
    config = GitHubConfigService(config_dir=get_app_config_dir()).get_github_config()
    return build_github_cli_env(config.token)


async def _resolve_gh_path() -> Path | None:
    try:
        return await get_gh_path()
    except Exception:
        return None


def _resolve_gh_path_sync() -> Path | None:
    try:
        return asyncio.run(get_gh_path())
    except Exception:
        return None


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)


async def build_shell_env(
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    shell_env = build_subprocess_env(base_env=os.environ, extra_env=env)
    shell_env.update(_load_github_cli_env())
    gh_path = await _resolve_gh_path()
    if gh_path is not None:
        shell_env["PATH"] = _prepend_to_path(shell_env.get("PATH"), gh_path.parent)
    return _sanitize_bash_env(shell_env)


def build_shell_env_sync(
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    shell_env = build_subprocess_env(base_env=os.environ, extra_env=env)
    shell_env.update(_load_github_cli_env())
    gh_path = _resolve_gh_path_sync()
    if gh_path is not None:
        shell_env["PATH"] = _prepend_to_path(shell_env.get("PATH"), gh_path.parent)
    return _sanitize_bash_env(shell_env)


async def create_shell_subprocess(
    *,
    command: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    stdin: int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
) -> asyncio.subprocess.Process:
    bash = resolve_bash_path()
    shell_env = await build_shell_env(env)
    return await asyncio.create_subprocess_exec(
        bash,
        "-lc",
        command,
        cwd=str(cwd),
        env=shell_env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=_start_new_session(),
        creationflags=_creation_flags(),
    )
