from __future__ import annotations

import asyncio
from pathlib import Path
import signal
import os

import pytest

from relay_teams.sessions.runs.background_tasks import command_runtime as runtime_module
from relay_teams.sessions.runs.background_tasks.command_runtime import (
    CommandRuntimeKind,
    ResolvedCommandRuntime,
    _AsyncProcessWriter,
    create_prepared_subprocess,
    kill_process_tree_by_pid,
    build_command_env,
    resolve_command_runtime,
)


class _FakePipeProcess:
    pid: int | None = 1234
    returncode: int | None = None
    stdin: _AsyncProcessWriter | None = None
    stdout: asyncio.StreamReader | None = None
    stderr: asyncio.StreamReader | None = None

    async def wait(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


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


@pytest.mark.asyncio
async def test_build_command_env_does_not_download_gh_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bash_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.BASH,
        executable="/bin/bash",
        display_name="Bash",
    )

    monkeypatch.setattr(
        runtime_module,
        "resolve_existing_gh_path",
        lambda: None,
    )
    monkeypatch.setattr(
        runtime_module,
        "_load_github_cli_env",
        lambda: {
            "GH_TOKEN": "ghp_secret",
            "GITHUB_TOKEN": "ghp_secret",
            "GH_PROMPT_DISABLED": "1",
        },
    )
    monkeypatch.setattr(
        runtime_module,
        "_load_clawhub_cli_env",
        lambda: {"CLAWHUB_TOKEN": "ch_secret"},
    )
    monkeypatch.setattr(
        runtime_module,
        "resolve_existing_clawhub_path",
        lambda: None,
    )
    monkeypatch.setattr(
        runtime_module.os,
        "environ",
        {"PATH": "/usr/bin"},
    )

    env = await build_command_env(
        {"EXTRA_VAR": "1"},
        runtime=bash_runtime,
        command="node script.js",
    )

    assert env["GH_TOKEN"] == "ghp_secret"
    assert env["GITHUB_TOKEN"] == "ghp_secret"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert env["CLAWHUB_TOKEN"] == "ch_secret"
    assert env["EXTRA_VAR"] == "1"
    assert env["PATH"] == "/usr/bin"


@pytest.mark.asyncio
async def test_build_command_env_includes_node_proxy_runtime_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bash_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.BASH,
        executable="/bin/bash",
        display_name="Bash",
    )

    monkeypatch.setattr(
        runtime_module,
        "resolve_existing_gh_path",
        lambda: None,
    )
    monkeypatch.setattr(
        runtime_module,
        "resolve_existing_clawhub_path",
        lambda: None,
    )
    monkeypatch.setattr(runtime_module, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(runtime_module, "_load_clawhub_cli_env", lambda: {})
    monkeypatch.setattr(
        runtime_module.os,
        "environ",
        {
            "PATH": "/usr/bin",
            "HTTP_PROXY": "http://proxy.example:8080",
            "NO_PROXY": "localhost,127.0.0.1",
        },
    )

    env = await build_command_env(
        runtime=bash_runtime,
        command="node script.js",
    )

    assert env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["npm_config_proxy"] == "http://proxy.example:8080"
    assert env["npm_config_https_proxy"] == "http://proxy.example:8080"
    assert env["npm_config_noproxy"] == "localhost,127.0.0.1"


@pytest.mark.asyncio
async def test_build_command_env_prepends_existing_gh_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    bash_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.BASH,
        executable="/bin/bash",
        display_name="Bash",
    )
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(
        runtime_module,
        "resolve_existing_gh_path",
        lambda: gh,
    )
    monkeypatch.setattr(runtime_module, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(runtime_module, "_load_clawhub_cli_env", lambda: {})
    monkeypatch.setattr(
        runtime_module.os,
        "environ",
        {"PATH": "/usr/bin"},
    )

    env = await build_command_env(
        runtime=bash_runtime,
        command="gh auth status",
    )

    assert env["PATH"].split(os.pathsep)[0] == str(gh.parent)


@pytest.mark.asyncio
async def test_build_command_env_prepends_existing_clawhub_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    bash_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.BASH,
        executable="/bin/bash",
        display_name="Bash",
    )
    clawhub = tmp_path / "bin" / "clawhub"
    clawhub.parent.mkdir()
    clawhub.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(
        runtime_module,
        "resolve_existing_gh_path",
        lambda: None,
    )
    monkeypatch.setattr(
        runtime_module,
        "resolve_existing_clawhub_path",
        lambda: clawhub,
    )
    monkeypatch.setattr(runtime_module, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(
        runtime_module,
        "_load_clawhub_cli_env",
        lambda: {"CLAWHUB_SITE": "https://mirror-cn.clawhub.com"},
    )
    monkeypatch.setattr(
        runtime_module.os,
        "environ",
        {"PATH": "/usr/bin"},
    )

    env = await build_command_env(
        runtime=bash_runtime,
        command="clawhub --version",
    )

    assert env["PATH"].split(os.pathsep)[0] == str(clawhub.parent)


@pytest.mark.asyncio
async def test_build_command_env_ignores_gh_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bash_runtime = ResolvedCommandRuntime(
        kind=CommandRuntimeKind.BASH,
        executable="/bin/bash",
        display_name="Bash",
    )

    monkeypatch.setattr(
        runtime_module,
        "resolve_existing_gh_path",
        lambda: (_ for _ in ()).throw(OSError("read-only")),
    )
    monkeypatch.setattr(runtime_module, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(runtime_module, "_load_clawhub_cli_env", lambda: {})
    monkeypatch.setattr(
        runtime_module.os,
        "environ",
        {"PATH": "/usr/bin"},
    )

    env = await build_command_env(
        runtime=bash_runtime,
        command="node script.js",
    )

    assert env["PATH"] == "/usr/bin"


@pytest.mark.asyncio
async def test_create_prepared_subprocess_uses_threaded_fallback_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_process = _FakePipeProcess()
    threaded_calls: list[
        tuple[tuple[str, ...], Path | None, dict[str, str] | None]
    ] = []

    async def fail_create_subprocess_exec(
        *_args: str,
        **_kwargs: object,
    ) -> object:
        raise NotImplementedError

    def fake_create_threaded_subprocess(
        *,
        argv: tuple[str, ...],
        cwd: Path | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        loop: asyncio.AbstractEventLoop,
    ) -> _FakePipeProcess:
        _ = (stdin, stdout, stderr, loop)
        threaded_calls.append((argv, cwd, env))
        return fake_process

    monkeypatch.setattr(
        runtime_module.asyncio, "create_subprocess_exec", fail_create_subprocess_exec
    )
    monkeypatch.setattr(runtime_module, "_is_windows", lambda: True)
    monkeypatch.setattr(
        runtime_module,
        "_create_threaded_subprocess",
        fake_create_threaded_subprocess,
    )

    proc = await create_prepared_subprocess(
        argv=("ssh", "prod", "pwd"),
        cwd=tmp_path,
        env={"SSH_AUTH_SOCK": "agent"},
    )

    assert proc is fake_process
    assert threaded_calls == [
        (("ssh", "prod", "pwd"), tmp_path, {"SSH_AUTH_SOCK": "agent"})
    ]


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
