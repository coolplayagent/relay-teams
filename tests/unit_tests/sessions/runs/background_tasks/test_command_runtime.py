from __future__ import annotations

from pathlib import Path
import signal
from unittest.mock import AsyncMock

import pytest
import agent_teams.env.python_env as python_env_module

from agent_teams.env import AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY
from agent_teams.sessions.runs.background_tasks import command_runtime as runtime_module
from agent_teams.sessions.runs.background_tasks.command_runtime import (
    CommandRuntimeKind,
    ResolvedCommandRuntime,
    kill_process_tree_by_pid,
    resolve_command_runtime,
)


def test_resolve_command_runtime_prefers_powershell_for_windows_cmdlets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bash_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.BASH,
        executable=r"C:\\Program Files\\Git\\bin\\bash.exe",
        display_name="Git Bash",
    )
    powershell_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.POWERSHELL,
        executable="powershell.exe",
        display_name="PowerShell",
    )

    monkeypatch.setattr(runtime_module, "_is_windows", lambda: True)
    monkeypatch.setattr(
        runtime_module, "resolve_bash_path", lambda: bash_runtime.executable
    )
    monkeypatch.setattr(
        runtime_module, "_build_powershell_runtime", lambda: powershell_runtime
    )
    monkeypatch.setattr(
        runtime_module,
        "_build_bash_runtime",
        lambda path, *, display_name: bash_runtime,
    )

    resolved = resolve_command_runtime(
        command="Write-Output 'ready'; Start-Sleep -Seconds 1"
    )

    assert resolved == powershell_runtime


def test_resolve_command_runtime_preserves_explicit_shell_wrappers_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bash_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.BASH,
        executable=r"C:\\Program Files\\Git\\bin\\bash.exe",
        display_name="Git Bash",
    )
    powershell_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.POWERSHELL,
        executable="powershell.exe",
        display_name="PowerShell",
    )

    monkeypatch.setattr(runtime_module, "_is_windows", lambda: True)
    monkeypatch.setattr(
        runtime_module, "resolve_bash_path", lambda: bash_runtime.executable
    )
    monkeypatch.setattr(
        runtime_module, "_build_powershell_runtime", lambda: powershell_runtime
    )
    monkeypatch.setattr(
        runtime_module,
        "_build_bash_runtime",
        lambda path, *, display_name: bash_runtime,
    )

    resolved = resolve_command_runtime(
        command='cmd /d /c "echo CMD_BG_READY & powershell -NoProfile -Command Start-Sleep -Seconds 1"'
    )

    assert resolved == bash_runtime


def test_resolve_command_runtime_prefers_powershell_for_env_and_member_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    powershell_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.POWERSHELL,
        executable="powershell.exe",
        display_name="PowerShell",
    )

    monkeypatch.setattr(runtime_module, "_is_windows", lambda: True)
    monkeypatch.setattr(
        runtime_module, "_build_powershell_runtime", lambda: powershell_runtime
    )

    assert (
        resolve_command_runtime(command="$env:DEMO='1'; Write-Output $env:DEMO")
        == powershell_runtime
    )
    assert (
        resolve_command_runtime(
            command="[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)"
        )
        == powershell_runtime
    )


def test_kill_process_tree_by_pid_waits_for_posix_exit_before_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[signal.Signals] = []
    wait_calls: list[float] = []

    monkeypatch.setattr(runtime_module, "_is_windows", lambda: False)
    monkeypatch.setattr(
        runtime_module,
        "_wait_for_process_group_exit",
        lambda pid, *, timeout_seconds: (
            wait_calls.append(timeout_seconds),
            True,
        )[1],
    )
    monkeypatch.setattr(
        runtime_module.os,
        "killpg",
        lambda pid, sig: signals.append(sig),
        raising=False,
    )

    assert kill_process_tree_by_pid(3210) is True
    assert signals == [signal.SIGTERM]
    assert wait_calls == [runtime_module._SIGKILL_GRACE_SECONDS]


def test_kill_process_tree_by_pid_requires_posix_exit_after_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[signal.Signals] = []
    wait_results = iter((False, False))
    wait_calls: list[float] = []

    monkeypatch.setattr(runtime_module, "_is_windows", lambda: False)
    monkeypatch.setattr(
        runtime_module,
        "_wait_for_process_group_exit",
        lambda pid, *, timeout_seconds: (
            wait_calls.append(timeout_seconds),
            next(wait_results),
        )[1],
    )
    monkeypatch.setattr(
        runtime_module.os,
        "killpg",
        lambda pid, sig: signals.append(sig),
        raising=False,
    )

    assert kill_process_tree_by_pid(3210) is False
    assert signals == [signal.SIGTERM, runtime_module._SIGKILL_SIGNAL]
    assert wait_calls == [runtime_module._SIGKILL_GRACE_SECONDS, 2]


@pytest.mark.asyncio
async def test_build_command_env_prefers_python_from_target_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    system_python = tmp_path / "system" / "python"
    system_python.parent.mkdir(parents=True)
    system_python.write_text("", encoding="utf-8")
    fallback_python = tmp_path / "fallback" / "python"
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(runtime_module, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(
        runtime_module,
        "_resolve_gh_path",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        runtime_module.os, "environ", {"PATH": str(system_python.parent)}
    )
    monkeypatch.setattr(
        python_env_module.shutil,
        "which",
        lambda command, path=None: (
            str(system_python)
            if command == "python" and path == str(system_python.parent)
            else None
        ),
    )
    monkeypatch.setattr(python_env_module.sys, "executable", str(fallback_python))

    env = await runtime_module.build_command_env(
        runtime=ResolvedCommandRuntime(
            kind=CommandRuntimeKind.BASH,
            executable="bash",
            display_name="Bash",
        )
    )

    assert env[AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY] == str(system_python.resolve())
    assert env["PATH"] == str(system_python.parent)


@pytest.mark.asyncio
async def test_build_command_env_does_not_duplicate_current_python_dir_at_path_front(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fallback_python = tmp_path / "fallback" / "python"
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(runtime_module, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(
        runtime_module,
        "_resolve_gh_path",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        runtime_module.os,
        "environ",
        {"PATH": str(fallback_python.parent)},
    )
    monkeypatch.setattr(
        python_env_module.shutil,
        "which",
        lambda command, path=None: None,
    )
    monkeypatch.setattr(python_env_module.sys, "executable", str(fallback_python))

    env = await runtime_module.build_command_env(
        runtime=ResolvedCommandRuntime(
            kind=CommandRuntimeKind.POWERSHELL,
            executable="powershell.exe",
            display_name="PowerShell",
        )
    )

    assert env[AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY] == str(fallback_python.resolve())
    assert env["PATH"] == str(fallback_python.parent)


@pytest.mark.asyncio
async def test_threaded_process_writer_defers_blocking_write_to_drain() -> None:
    writes: list[bytes] = []
    flush_count = 0

    class _FakeStream:
        def write(self, data: bytes) -> None:
            writes.append(data)

        def flush(self) -> None:
            nonlocal flush_count
            flush_count += 1

        def close(self) -> None:
            return None

    writer = runtime_module._ThreadedProcessWriter(_FakeStream())
    writer.write(b"hello ")
    writer.write(b"world")

    assert writes == []
    assert flush_count == 0

    await writer.drain()

    assert writes == [b"hello world"]
    assert flush_count == 1
