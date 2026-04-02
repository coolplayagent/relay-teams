from __future__ import annotations

import signal

import pytest

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
    )

    assert kill_process_tree_by_pid(3210) is False
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert wait_calls == [runtime_module._SIGKILL_GRACE_SECONDS, 2]
