# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from enum import Enum
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from pydantic import BaseModel, ConfigDict

from agent_teams.env import build_github_cli_env, build_subprocess_env, get_env_var
from agent_teams.env.github_config_service import GitHubConfigService
from agent_teams.env.runtime_env import get_app_config_dir
from agent_teams.sessions.runs.background_tasks.github_cli import get_gh_path

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
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 1_200_000


class CommandRuntimeKind(str, Enum):
    BASH = "bash"
    POWERSHELL = "powershell"


class ResolvedCommandRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: CommandRuntimeKind
    executable: str
    display_name: str


def resolve_bash_path() -> str:
    env_path = get_env_var("GIT_BASH_PATH")
    if env_path:
        resolved_env_path = Path(env_path).expanduser()
        if resolved_env_path.is_file():
            return str(resolved_env_path)

    if _is_windows():
        return _resolve_windows_bash_path()
    return _resolve_posix_bash_path()


def resolve_command_runtime() -> ResolvedCommandRuntime:
    if _is_windows():
        try:
            return _build_bash_runtime(resolve_bash_path(), display_name="Git Bash")
        except FileNotFoundError:
            return _build_powershell_runtime()
    return _build_bash_runtime(resolve_bash_path(), display_name="Bash")


def build_command_argv(
    *,
    runtime: ResolvedCommandRuntime,
    command: str,
    login: bool = False,
) -> tuple[str, ...]:
    if runtime.kind == CommandRuntimeKind.BASH:
        return (runtime.executable, "-lc", command)
    argv = [runtime.executable]
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


def normalize_timeout(timeout_ms: int | None) -> int:
    if timeout_ms is None:
        return DEFAULT_TIMEOUT_MS
    if timeout_ms < 1:
        raise ValueError("timeout_ms must be >= 1")
    if timeout_ms > MAX_TIMEOUT_MS:
        return MAX_TIMEOUT_MS
    return timeout_ms


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


def _build_bash_runtime(path: str, *, display_name: str) -> ResolvedCommandRuntime:
    return ResolvedCommandRuntime(
        kind=CommandRuntimeKind.BASH,
        executable=path,
        display_name=display_name,
    )


def _build_powershell_runtime() -> ResolvedCommandRuntime:
    path = _resolve_powershell_path()
    executable_name = Path(path).name.lower()
    display_name = "PowerShell Core" if executable_name == "pwsh.exe" else "PowerShell"
    return ResolvedCommandRuntime(
        kind=CommandRuntimeKind.POWERSHELL,
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


def _creation_flags() -> int:
    if _is_windows():
        return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return 0


def _start_new_session() -> bool:
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


def _sanitize_command_env(
    env: dict[str, str],
    *,
    runtime: ResolvedCommandRuntime,
) -> dict[str, str]:
    if runtime.kind == CommandRuntimeKind.BASH:
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


def _load_github_cli_env() -> dict[str, str]:
    config = GitHubConfigService(config_dir=get_app_config_dir()).get_github_config()
    return build_github_cli_env(config.token)


async def _resolve_gh_path() -> Path | None:
    try:
        return await get_gh_path()
    except Exception:
        return None


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)


async def build_command_env(
    env: dict[str, str] | None = None,
    *,
    runtime: ResolvedCommandRuntime | None = None,
) -> dict[str, str]:
    resolved_runtime = runtime or resolve_command_runtime()
    command_env = build_subprocess_env(base_env=os.environ, extra_env=env)
    command_env.update(_load_github_cli_env())
    gh_path = await _resolve_gh_path()
    if gh_path is not None:
        command_env["PATH"] = _prepend_to_path(command_env.get("PATH"), gh_path.parent)
    return _sanitize_command_env(command_env, runtime=resolved_runtime)


async def create_command_subprocess(
    *,
    command: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    runtime: ResolvedCommandRuntime | None = None,
    login: bool = False,
    stdin: int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
) -> asyncio.subprocess.Process:
    resolved_runtime = runtime or resolve_command_runtime()
    command_env = await build_command_env(env, runtime=resolved_runtime)
    return await asyncio.create_subprocess_exec(
        *build_command_argv(
            runtime=resolved_runtime,
            command=command,
            login=login,
        ),
        cwd=str(cwd),
        env=command_env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=_start_new_session(),
        creationflags=_creation_flags(),
    )
