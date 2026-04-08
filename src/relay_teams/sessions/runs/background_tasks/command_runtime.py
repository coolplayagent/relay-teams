# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import contextlib
from enum import Enum
import importlib.util
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import IO, Callable, Protocol, cast

from pydantic import BaseModel, ConfigDict

from relay_teams.env import build_github_cli_env, build_subprocess_env, get_env_var
from relay_teams.env.github_config_service import GitHubConfigService
from relay_teams.env.runtime_env import get_app_config_dir
from relay_teams.sessions.runs.background_tasks.github_cli import (
    resolve_existing_gh_path,
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
_SIGKILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 1_200_000
_WINDOWS_POWERSHELL_CMDLET_PREFIXES = frozenset(
    {
        "Add",
        "Clear",
        "Convert",
        "Copy",
        "Disable",
        "Enable",
        "Export",
        "ForEach",
        "Get",
        "Import",
        "Invoke",
        "Join",
        "Measure",
        "Move",
        "New",
        "Out",
        "Read",
        "Remove",
        "Rename",
        "Restart",
        "Resume",
        "Select",
        "Set",
        "Sort",
        "Split",
        "Start",
        "Stop",
        "Suspend",
        "Test",
        "Wait",
        "Where",
        "Write",
    }
)
_WINDOWS_POWERSHELL_ALIASES = frozenset({"curl", "irm", "iwr", "wget"})
_EXPLICIT_WINDOWS_SHELL_NAMES = frozenset(
    {
        "bash",
        "bash.exe",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "sh",
        "sh.exe",
    }
)
_POWERSHELL_STATEMENT_PREFIX = r"(?:^|[;|&]\s*)"
_POWERSHELL_CMDLET_PATTERN = re.compile(
    _POWERSHELL_STATEMENT_PREFIX + r"(?P<cmdlet>[A-Za-z]+-[A-Za-z][\w-]*)\b"
)
_POWERSHELL_ENV_PATTERN = re.compile(
    _POWERSHELL_STATEMENT_PREFIX + r"\$env:[A-Za-z_][\w]*"
)
_POWERSHELL_MEMBER_PATTERN = re.compile(
    _POWERSHELL_STATEMENT_PREFIX + r"\[[A-Za-z_][A-Za-z0-9_\.\[\]]*\]::"
)


class CommandRuntimeKind(str, Enum):
    BASH = "bash"
    POWERSHELL = "powershell"


class ResolvedCommandRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: CommandRuntimeKind
    executable: str
    display_name: str


class _AsyncProcessWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...


class _PipeProcess(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def returncode(self) -> int | None: ...

    @property
    def stdin(self) -> _AsyncProcessWriter | None: ...

    @property
    def stdout(self) -> asyncio.StreamReader | None: ...

    @property
    def stderr(self) -> asyncio.StreamReader | None: ...

    async def wait(self) -> int | None: ...

    def kill(self) -> None: ...


class _WritableBinaryStream(Protocol):
    def write(self, data: bytes) -> object: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class _ThreadedProcessWriter:
    def __init__(self, stream: _WritableBinaryStream) -> None:
        self._stream = stream
        self._pending = bytearray()
        self._pending_lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self._pending_lock:
            self._pending.extend(data)

    async def drain(self) -> None:
        payload = self._take_pending()
        if not payload:
            return None
        await asyncio.to_thread(self._write_and_flush, payload)

    def close(self) -> None:
        self._stream.close()

    def _take_pending(self) -> bytes:
        with self._pending_lock:
            if not self._pending:
                return b""
            payload = bytes(self._pending)
            self._pending.clear()
            return payload

    def _write_and_flush(self, payload: bytes) -> None:
        self._stream.write(payload)
        self._stream.flush()


class _ThreadedProcessAdapter:
    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        *,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._proc = proc
        self.stdin = (
            _ThreadedProcessWriter(cast(_WritableBinaryStream, proc.stdin))
            if proc.stdin is not None
            else None
        )
        self.stdout = asyncio.StreamReader() if proc.stdout is not None else None
        self.stderr = asyncio.StreamReader() if proc.stderr is not None else None
        self._wait_future: asyncio.Future[int | None] = loop.create_future()
        self._start_reader_thread(proc.stdout, self.stdout, loop)
        self._start_reader_thread(proc.stderr, self.stderr, loop)
        threading.Thread(
            target=self._wait_for_process,
            args=(loop,),
            daemon=True,
        ).start()

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    async def wait(self) -> int | None:
        return await asyncio.shield(self._wait_future)

    def kill(self) -> None:
        self._proc.kill()

    def _start_reader_thread(
        self,
        pipe: IO[bytes] | None,
        reader: asyncio.StreamReader | None,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        if pipe is None or reader is None:
            return
        threading.Thread(
            target=self._pump_pipe,
            args=(pipe, reader, loop),
            daemon=True,
        ).start()

    @staticmethod
    def _pump_pipe(
        pipe: IO[bytes],
        reader: asyncio.StreamReader,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        try:
            while True:
                chunk = _read_pipe_chunk(pipe, 4096)
                if not chunk:
                    break
                loop.call_soon_threadsafe(reader.feed_data, chunk)
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(reader.feed_eof)
            with contextlib.suppress(Exception):
                pipe.close()

    def _wait_for_process(self, loop: asyncio.AbstractEventLoop) -> None:
        exit_code = self._proc.wait()
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._resolve_wait_future, exit_code)

    def _resolve_wait_future(self, exit_code: int) -> None:
        if not self._wait_future.done():
            self._wait_future.set_result(exit_code)


def resolve_bash_path() -> str:
    env_path = get_env_var("GIT_BASH_PATH")
    if env_path:
        resolved_env_path = Path(env_path).expanduser()
        if resolved_env_path.is_file():
            return str(resolved_env_path)

    if _is_windows():
        return _resolve_windows_bash_path()
    return _resolve_posix_bash_path()


def resolve_command_runtime(*, command: str | None = None) -> ResolvedCommandRuntime:
    if _is_windows():
        if _command_prefers_powershell(command):
            return _build_powershell_runtime()
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


def _command_prefers_powershell(command: str | None) -> bool:
    if not _is_windows() or command is None:
        return False
    normalized = command.strip()
    if not normalized:
        return False
    first_token = normalized.split(maxsplit=1)[0].strip().strip("\"'")
    command_name = Path(first_token).name.lower()
    if command_name in _EXPLICIT_WINDOWS_SHELL_NAMES:
        return False
    if _POWERSHELL_ENV_PATTERN.search(normalized) is not None:
        return True
    if _POWERSHELL_MEMBER_PATTERN.search(normalized) is not None:
        return True
    if command_name in _WINDOWS_POWERSHELL_ALIASES:
        return True
    if _starts_powershell_script_invocation(normalized):
        return True
    for match in _POWERSHELL_CMDLET_PATTERN.finditer(normalized):
        cmdlet = match.group("cmdlet")
        prefix = cmdlet.split("-", 1)[0]
        if prefix in _WINDOWS_POWERSHELL_CMDLET_PREFIXES:
            return True
    return False


def _starts_powershell_script_invocation(command: str) -> bool:
    normalized = command.strip()
    if not normalized:
        return False
    if normalized.startswith("&"):
        remainder = normalized[1:].lstrip()
        token = _extract_windows_shell_token(remainder)
        return token.lower().endswith(".ps1")
    token = _extract_windows_shell_token(normalized)
    return token.lower().endswith(".ps1")


def _extract_windows_shell_token(command: str) -> str:
    stripped = command.lstrip()
    if not stripped:
        return ""
    quote = stripped[0]
    if quote in {"'", '"'}:
        end_index = stripped.find(quote, 1)
        if end_index == -1:
            return stripped[1:]
        return stripped[1:end_index]
    token, *_ = stripped.split(maxsplit=1)
    return token


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


def _signal_process_group(pid: int, sig: int) -> None:
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        raise ProcessLookupError(pid)
    killpg(pid, sig)


def kill_process_tree_by_pid(pid: int) -> bool:
    if _is_windows():
        try:
            completed = subprocess.run(
                ["taskkill", "/f", "/t", "/pid", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_SIGKILL_GRACE_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    try:
        _signal_process_group(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    if _wait_for_process_group_exit(pid, timeout_seconds=_SIGKILL_GRACE_SECONDS):
        return True
    try:
        _signal_process_group(pid, _SIGKILL_SIGNAL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return _wait_for_process_group_exit(pid, timeout_seconds=2)


async def _kill_process_tree_by_pid(pid: int) -> bool:
    return await asyncio.to_thread(kill_process_tree_by_pid, pid)


def _wait_for_process_group_exit(pid: int, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(timeout_seconds, 0)
    while True:
        if not _process_group_exists(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        remaining = max(0.0, deadline - time.monotonic())
        time.sleep(min(0.05, remaining))


def _process_group_exists(pid: int) -> bool:
    try:
        _signal_process_group(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _kill_process_tree(proc: _PipeProcess) -> None:
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
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_SIGKILL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(ProcessLookupError):
                await asyncio.wait_for(proc.wait(), timeout=2)
        return

    try:
        _signal_process_group(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=_SIGKILL_GRACE_SECONDS)
    except asyncio.TimeoutError:
        try:
            _signal_process_group(pid, _SIGKILL_SIGNAL)
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
        return resolve_existing_gh_path()
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
    command: str | None = None,
) -> dict[str, str]:
    resolved_runtime = runtime or resolve_command_runtime(command=command)
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
) -> _PipeProcess:
    resolved_runtime = runtime or resolve_command_runtime(command=command)
    command_env = await build_command_env(
        env,
        runtime=resolved_runtime,
        command=command,
    )
    argv = build_command_argv(
        runtime=resolved_runtime,
        command=command,
        login=login,
    )
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=command_env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=_start_new_session(),
            creationflags=_creation_flags(),
        )
    except NotImplementedError:
        if not _is_windows():
            raise
        loop = asyncio.get_running_loop()
        return _create_threaded_subprocess(
            argv=argv,
            cwd=cwd,
            env=command_env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            loop=loop,
        )


def _create_threaded_subprocess(
    *,
    argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    stdin: int | None,
    stdout: int | None,
    stderr: int | None,
    loop: asyncio.AbstractEventLoop,
) -> _PipeProcess:
    proc = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=_start_new_session(),
        creationflags=_creation_flags(),
    )
    return _ThreadedProcessAdapter(proc, loop=loop)


def _read_pipe_chunk(pipe: IO[bytes], size: int) -> bytes:
    read1 = cast(Callable[[int], bytes] | None, getattr(pipe, "read1", None))
    if read1 is not None:
        return read1(size)
    return pipe.read(size)
