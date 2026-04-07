# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from enum import Enum
import importlib.util
import os
import platform
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys

from pydantic import BaseModel, ConfigDict

from relay_teams.env import build_github_cli_env, build_subprocess_env, get_env_var
from relay_teams.env.github_config_service import GitHubConfigService
from relay_teams.env.runtime_env import get_app_config_dir
from relay_teams.tools.workspace_tools.github_cli import resolve_existing_gh_path
from relay_teams.tools.workspace_tools.shell_policy import (
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
_SIGKILL_GRACE_SECONDS = 5


class ShellKind(str, Enum):
    BASH = "bash"
    POWERSHELL = "powershell"


class ResolvedShell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ShellKind
    executable: str
    display_name: str


class ShellRuntimeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shell_info: str
    shell_path: str


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


def resolve_exec_shell() -> ResolvedShell:
    if _is_windows():
        try:
            return _build_bash_shell(resolve_bash_path(), display_name="Git Bash")
        except FileNotFoundError:
            return _build_powershell_shell()
    return _build_bash_shell(resolve_bash_path(), display_name="Bash")


def describe_runtime_shell() -> ShellRuntimeSummary:
    try:
        shell = resolve_exec_shell()
    except Exception:
        return ShellRuntimeSummary(shell_info="Unknown", shell_path="Unknown")

    system = platform.system()
    if shell.kind == ShellKind.POWERSHELL:
        return ShellRuntimeSummary(
            shell_info="PowerShell",
            shell_path=shell.executable,
        )
    if system == "Windows":
        msystem = os.environ.get("MSYSTEM")
        if msystem:
            return ShellRuntimeSummary(
                shell_info=f"Git Bash ({msystem})",
                shell_path=shell.executable,
            )
        return ShellRuntimeSummary(
            shell_info="Git Bash",
            shell_path=shell.executable,
        )
    if system == "Linux":
        release = platform.release().lower()
        version = platform.version().lower()
        label = (
            "WSL (Linux Bash)"
            if "microsoft" in release or "microsoft" in version
            else "Native Linux Bash"
        )
        return ShellRuntimeSummary(shell_info=label, shell_path=shell.executable)
    if system == "Darwin":
        return ShellRuntimeSummary(
            shell_info="macOS Bash",
            shell_path=shell.executable,
        )
    return ShellRuntimeSummary(
        shell_info=shell.display_name,
        shell_path=shell.executable,
    )


def build_shell_command(
    *,
    shell: ResolvedShell,
    command: str,
    login: bool = False,
) -> tuple[str, ...]:
    if shell.kind == ShellKind.BASH:
        return (shell.executable, "-lc", command)
    argv = [shell.executable]
    if not login:
        argv.append("-NoProfile")
    argv.extend(["-Command", _prepend_powershell_utf8_prefix(command)])
    return tuple(argv)


def windows_conpty_supported() -> bool:
    build_number = _windows_build_number()
    return (
        _is_windows()
        and build_number is not None
        and build_number >= 17763
        and importlib.util.find_spec("winpty") is not None
    )


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


def _resolve_powershell_path() -> str:
    env_path = get_env_var("POWERSHELL_PATH")
    if env_path:
        resolved_env_path = Path(env_path).expanduser()
        if resolved_env_path.is_file():
            return str(resolved_env_path)
    for command_name in ("pwsh", "powershell"):
        resolved = shutil.which(command_name)
        if resolved:
            return resolved
    raise FileNotFoundError(
        "PowerShell executable not found; install PowerShell or set POWERSHELL_PATH"
    )


def _build_bash_shell(path: str, *, display_name: str) -> ResolvedShell:
    return ResolvedShell(
        kind=ShellKind.BASH,
        executable=path,
        display_name=display_name,
    )


def _build_powershell_shell() -> ResolvedShell:
    path = _resolve_powershell_path()
    executable_name = Path(path).name.lower()
    display_name = "PowerShell Core" if executable_name == "pwsh.exe" else "PowerShell"
    return ResolvedShell(
        kind=ShellKind.POWERSHELL,
        executable=path,
        display_name=display_name,
    )


def _windows_build_number() -> int | None:
    if not _is_windows():
        return None
    try:
        return int(sys.getwindowsversion().build)
    except (AttributeError, TypeError, ValueError):
        return None


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


def _sanitize_shell_env(
    env: dict[str, str],
    *,
    shell: ResolvedShell,
) -> dict[str, str]:
    if shell.kind == ShellKind.BASH:
        return _sanitize_bash_env(env)
    return env


def _prepend_powershell_utf8_prefix(command: str) -> str:
    return "\n".join(
        (
            "[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)",
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
            "$OutputEncoding = [Console]::OutputEncoding",
            command,
        )
    )


async def _kill_process_tree_by_pid(pid: int) -> bool:
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
            exit_code = await asyncio.wait_for(
                killer.wait(), timeout=_SIGKILL_GRACE_SECONDS
            )
        except (OSError, asyncio.TimeoutError):
            return False
        return exit_code == 0

    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    return True


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate the process and its entire process tree."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    if pid is None:
        return

    if _is_windows():
        try:
            killed = await _kill_process_tree_by_pid(pid)
        except Exception:
            killed = False
        if not killed:
            proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_SIGKILL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=2)
        return

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
    shell = _build_bash_shell(bash, display_name="Git Bash")
    shell_env = build_shell_env_sync(shell=shell)
    try:
        proc = subprocess.run(
            list(build_shell_command(shell=shell, command=command)),
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
        return resolve_existing_gh_path()
    except Exception:
        return None


def _resolve_gh_path_sync() -> Path | None:
    try:
        return resolve_existing_gh_path()
    except Exception:
        return None


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)


async def build_shell_env(
    env: dict[str, str] | None = None,
    *,
    shell: ResolvedShell | None = None,
) -> dict[str, str]:
    resolved_shell = shell or resolve_exec_shell()
    shell_env = build_subprocess_env(base_env=os.environ, extra_env=env)
    shell_env.update(_load_github_cli_env())
    gh_path = await _resolve_gh_path()
    if gh_path is not None:
        shell_env["PATH"] = _prepend_to_path(shell_env.get("PATH"), gh_path.parent)
    return _sanitize_shell_env(shell_env, shell=resolved_shell)


def build_shell_env_sync(
    env: dict[str, str] | None = None,
    *,
    shell: ResolvedShell | None = None,
) -> dict[str, str]:
    resolved_shell = shell or resolve_exec_shell()
    shell_env = build_subprocess_env(base_env=os.environ, extra_env=env)
    shell_env.update(_load_github_cli_env())
    gh_path = _resolve_gh_path_sync()
    if gh_path is not None:
        shell_env["PATH"] = _prepend_to_path(shell_env.get("PATH"), gh_path.parent)
    return _sanitize_shell_env(shell_env, shell=resolved_shell)


async def create_shell_subprocess(
    *,
    command: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    shell: ResolvedShell | None = None,
    login: bool = False,
    stdin: int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
) -> asyncio.subprocess.Process:
    resolved_shell = shell or resolve_exec_shell()
    shell_env = await build_shell_env(env, shell=resolved_shell)
    return await asyncio.create_subprocess_exec(
        *build_shell_command(
            shell=resolved_shell,
            command=command,
            login=login,
        ),
        cwd=str(cwd),
        env=shell_env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=_start_new_session(),
        creationflags=_creation_flags(),
    )
