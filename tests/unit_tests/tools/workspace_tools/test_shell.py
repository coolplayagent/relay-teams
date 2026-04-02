# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
from typing import cast
from unittest.mock import AsyncMock

import pytest
import agent_teams.env.python_env as python_env_module

from agent_teams.env import AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY


class TestExtractPathsFromCommand:
    def test_extract_cd_path(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("cd /tmp/test")
        assert "/tmp/test" in paths

    def test_extract_rm_path(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("rm -rf /tmp/test")
        assert "/tmp/test" in paths

    def test_extract_mkdir_path(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("mkdir -p /tmp/newdir/subdir")
        assert "/tmp/newdir/subdir" in paths

    def test_extract_multiple_commands(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("cd /project")
        assert "/project" in paths

        paths = extract_paths_from_command("rm -rf /project/build")
        assert "/project/build" in paths

        paths = extract_paths_from_command("mkdir /project/dist")
        assert "/project/dist" in paths

    def test_ignore_flags(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("rm -rf -force /tmp/test")

        assert "/tmp/test" in paths

    def test_quoted_paths(self):
        from agent_teams.tools.workspace_tools.shell_executor import (
            extract_paths_from_command,
        )

        paths = extract_paths_from_command("cd '/path with spaces'")

        assert "'/path with spaces'" in paths or "/path with spaces" in paths


class TestNormalizeTimeout:
    def test_none_timeout(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        result = normalize_timeout(None)
        assert result == 120_000

    def test_custom_timeout(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        result = normalize_timeout(60000)
        assert result == 60000

    def test_timeout_too_large(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        result = normalize_timeout(1_300_000)
        assert result == 1_200_000

    def test_timeout_too_small(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        with pytest.raises(ValueError):
            normalize_timeout(0)

    def test_negative_timeout(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        with pytest.raises(ValueError):
            normalize_timeout(-1)


def test_resolve_bash_path_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    git_bash = tmp_path / "custom" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_BASH_PATH", str(git_bash))

    assert shell_executor.resolve_bash_path() == str(git_bash)


def test_resolve_bash_path_prefers_git_bash_over_wsl_on_windows(
    monkeypatch, tmp_path: Path
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    git_bash = tmp_path / "Git" / "bin" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.write_text("", encoding="utf-8")

    monkeypatch.delenv("GIT_BASH_PATH", raising=False)
    monkeypatch.setattr(shell_executor, "_is_windows", lambda: True)
    monkeypatch.setattr(
        shell_executor,
        "_iter_windows_git_bash_candidates",
        lambda: (git_bash,),
    )
    monkeypatch.setattr(
        shell_executor.shutil,
        "which",
        lambda name: r"C:\Windows\System32\bash.exe" if name == "bash" else None,
    )

    assert shell_executor.resolve_bash_path() == str(git_bash)


def test_iter_windows_git_bash_candidates_includes_git_install_root(
    monkeypatch, tmp_path: Path
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    git_exe = tmp_path / "Git" / "cmd" / "git.exe"
    git_exe.parent.mkdir(parents=True)
    git_exe.write_text("", encoding="utf-8")
    expected_paths = (
        tmp_path / "Git" / "bin" / "bash.exe",
        tmp_path / "Git" / "usr" / "bin" / "bash.exe",
    )

    monkeypatch.setattr(shell_executor, "WINDOWS_GIT_BASH_CANDIDATES", ())
    monkeypatch.setattr(
        shell_executor.shutil,
        "which",
        lambda name: str(git_exe) if name == "git" else None,
    )

    assert shell_executor._iter_windows_git_bash_candidates() == expected_paths


def test_resolve_bash_path_rejects_wsl_bash_without_git_bash(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    monkeypatch.delenv("GIT_BASH_PATH", raising=False)
    monkeypatch.setattr(shell_executor, "_is_windows", lambda: True)
    monkeypatch.setattr(shell_executor, "WINDOWS_GIT_BASH_CANDIDATES", ())
    monkeypatch.setattr(
        shell_executor.shutil,
        "which",
        lambda name: r"C:\Windows\System32\bash.exe" if name == "bash" else None,
    )

    with pytest.raises(FileNotFoundError):
        shell_executor.resolve_bash_path()


def test_resolve_bash_path_uses_system_bash_on_non_windows(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    monkeypatch.delenv("GIT_BASH_PATH", raising=False)
    monkeypatch.setattr(shell_executor, "_is_windows", lambda: False)
    monkeypatch.setattr(
        shell_executor.shutil,
        "which",
        lambda name: "/bin/bash" if name == "bash" else None,
    )

    assert shell_executor.resolve_bash_path() == "/bin/bash"


def test_resolve_exec_shell_falls_back_to_powershell_on_windows(
    monkeypatch,
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    monkeypatch.setattr(shell_executor, "_is_windows", lambda: True)
    monkeypatch.setattr(
        shell_executor,
        "resolve_bash_path",
        lambda: (_ for _ in ()).throw(FileNotFoundError("missing bash")),
    )
    monkeypatch.setattr(
        shell_executor.shutil,
        "which",
        lambda name: (
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
            if name == "powershell"
            else None
        ),
    )

    shell = shell_executor.resolve_exec_shell()

    assert shell.kind == shell_executor.ShellKind.POWERSHELL
    assert shell.executable.endswith("powershell.exe")


def test_describe_runtime_shell_reports_powershell_when_git_bash_is_missing(
    monkeypatch,
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    monkeypatch.setattr(shell_executor, "_is_windows", lambda: True)
    monkeypatch.setattr(
        shell_executor,
        "resolve_bash_path",
        lambda: (_ for _ in ()).throw(FileNotFoundError("missing bash")),
    )
    monkeypatch.setattr(
        shell_executor.shutil,
        "which",
        lambda name: (
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
            if name == "powershell"
            else None
        ),
    )

    summary = shell_executor.describe_runtime_shell()

    assert summary.shell_info == "PowerShell"
    assert summary.shell_path.endswith("powershell.exe")


def test_build_shell_env_sync_prefers_python_from_target_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    system_python = tmp_path / "system" / "python"
    system_python.parent.mkdir(parents=True)
    system_python.write_text("", encoding="utf-8")
    fallback_python = tmp_path / "fallback" / "python"
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(shell_executor, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(shell_executor, "_resolve_gh_path_sync", lambda: None)
    monkeypatch.setattr(
        shell_executor.os, "environ", {"PATH": str(system_python.parent)}
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

    env = shell_executor.build_shell_env_sync(
        shell=shell_executor.ResolvedShell(
            kind=shell_executor.ShellKind.BASH,
            executable="bash",
            display_name="Bash",
        )
    )

    assert env[AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY] == str(system_python.resolve())
    assert env["PATH"] == str(system_python.parent)


def test_build_shell_env_sync_does_not_duplicate_current_python_dir_at_path_front(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    fallback_python = tmp_path / "fallback" / "python"
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(shell_executor, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(shell_executor, "_resolve_gh_path_sync", lambda: None)
    monkeypatch.setattr(
        shell_executor.os,
        "environ",
        {"PATH": str(fallback_python.parent)},
    )
    monkeypatch.setattr(
        python_env_module.shutil,
        "which",
        lambda command, path=None: None,
    )
    monkeypatch.setattr(python_env_module.sys, "executable", str(fallback_python))

    env = shell_executor.build_shell_env_sync(
        shell=shell_executor.ResolvedShell(
            kind=shell_executor.ShellKind.BASH,
            executable="bash",
            display_name="Bash",
        )
    )

    assert env[AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY] == str(fallback_python.resolve())
    assert env["PATH"] == str(fallback_python.parent)


# ---------------------------------------------------------------------------
# spawn_shell: exit code + process group
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Minimal fake for asyncio.subprocess.Process."""

    def __init__(self, exit_code: int = 0) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self._exit_code = exit_code
        self._wait_event = asyncio.Event()
        self.pid = 12345

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15
            self._wait_event.set()

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9
            self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        assert self.returncode is not None
        return self.returncode


@pytest.mark.asyncio
async def test_kill_process_tree_windows_falls_back_to_proc_kill_when_taskkill_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    proc = _FakeProcess()
    monkeypatch.setattr(shell_executor, "_is_windows", lambda: True)

    async def _fake_kill_process_tree_by_pid(pid: int) -> bool:
        assert pid == proc.pid
        return False

    monkeypatch.setattr(
        shell_executor,
        "_kill_process_tree_by_pid",
        _fake_kill_process_tree_by_pid,
    )

    await shell_executor._kill_process_tree(cast(asyncio.subprocess.Process, proc))

    assert proc.returncode == -9


def _make_fake_factory(
    proc: _FakeProcess, feed_stdout: bytes = b"", feed_exit: int = 0
):
    """Return an async factory that feeds data then exits with given code."""

    async def fake_create_subprocess_exec(
        *args: object, **kwargs: object
    ) -> _FakeProcess:
        _ = (args, kwargs)

        async def _feed() -> None:
            if feed_stdout:
                proc.stdout.feed_data(feed_stdout)
            proc.stdout.feed_eof()
            proc.stderr.feed_eof()
            await asyncio.sleep(0.01)
            proc.returncode = feed_exit
            proc._wait_event.set()

        asyncio.create_task(_feed())
        return proc

    return fake_create_subprocess_exec


@pytest.mark.asyncio
async def test_spawn_shell_does_not_timeout_after_streams_finish(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    proc = _FakeProcess()
    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")
    monkeypatch.setattr(
        shell_executor.asyncio,
        "create_subprocess_exec",
        _make_fake_factory(proc, feed_stdout=b"/d/workspace/aider\n", feed_exit=0),
    )

    chunks = [
        item
        async for item in shell_executor.spawn_shell(
            command="pwd",
            cwd=Path("."),
            timeout_ms=100,
        )
    ]

    assert ("stdout", "/d/workspace/aider\n") in chunks
    assert ("exit_code", "0") in chunks
    assert proc.returncode == 0


@pytest.mark.asyncio
async def test_spawn_shell_yields_nonzero_exit_code(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    proc = _FakeProcess()
    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")
    monkeypatch.setattr(
        shell_executor.asyncio,
        "create_subprocess_exec",
        _make_fake_factory(proc, feed_stdout=b"error output\n", feed_exit=1),
    )

    chunks = [
        item
        async for item in shell_executor.spawn_shell(
            command="false",
            cwd=Path("."),
            timeout_ms=100,
        )
    ]

    assert ("stdout", "error output\n") in chunks
    assert ("exit_code", "1") in chunks
    assert proc.returncode == 1


@pytest.mark.asyncio
async def test_spawn_shell_creates_process_group(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    captured_kwargs: dict[str, object] = {}
    proc = _FakeProcess()

    async def capturing_factory(*args: object, **kwargs: object) -> _FakeProcess:
        captured_kwargs.update(kwargs)

        async def _feed() -> None:
            proc.stdout.feed_eof()
            proc.stderr.feed_eof()
            await asyncio.sleep(0.01)
            proc.returncode = 0
            proc._wait_event.set()

        asyncio.create_task(_feed())
        return proc

    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")
    monkeypatch.setattr(
        shell_executor.asyncio,
        "create_subprocess_exec",
        capturing_factory,
    )

    _ = [
        item
        async for item in shell_executor.spawn_shell(
            command="true",
            cwd=Path("."),
            timeout_ms=100,
        )
    ]

    if shell_executor._is_windows():
        assert captured_kwargs.get("creationflags", 0) != 0
    else:
        assert captured_kwargs.get("start_new_session") is True
    assert captured_kwargs.get("stdin") == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_spawn_shell_passes_role_env_to_subprocess(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    captured_kwargs: dict[str, object] = {}
    proc = _FakeProcess()

    async def capturing_factory(*args: object, **kwargs: object) -> _FakeProcess:
        captured_kwargs.update(kwargs)

        async def _feed() -> None:
            proc.stdout.feed_eof()
            proc.stderr.feed_eof()
            await asyncio.sleep(0.01)
            proc.returncode = 0
            proc._wait_event.set()

        asyncio.create_task(_feed())
        return proc

    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")
    monkeypatch.setattr(
        shell_executor.asyncio,
        "create_subprocess_exec",
        capturing_factory,
    )

    _ = [
        item
        async for item in shell_executor.spawn_shell(
            command="true",
            cwd=Path("."),
            timeout_ms=100,
            env={"AGENT_TEAMS_CURRENT_ROLE_ID": "Crafter"},
        )
    ]

    env = captured_kwargs.get("env")
    assert isinstance(env, dict)
    assert env["AGENT_TEAMS_CURRENT_ROLE_ID"] == "Crafter"


@pytest.mark.asyncio
async def test_spawn_shell_injects_github_token_and_bundled_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    captured_kwargs: dict[str, object] = {}
    proc = _FakeProcess()
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir(parents=True)
    gh.write_text("", encoding="utf-8")

    async def capturing_factory(*args: object, **kwargs: object) -> _FakeProcess:
        captured_kwargs.update(kwargs)

        async def _feed() -> None:
            proc.stdout.feed_eof()
            proc.stderr.feed_eof()
            await asyncio.sleep(0.01)
            proc.returncode = 0
            proc._wait_event.set()

        asyncio.create_task(_feed())
        return proc

    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")
    monkeypatch.setattr(
        shell_executor,
        "_load_github_cli_env",
        lambda: {
            "GH_TOKEN": "ghp_secret",
            "GITHUB_TOKEN": "ghp_secret",
            "GH_PROMPT_DISABLED": "1",
        },
    )
    monkeypatch.setattr(shell_executor, "_resolve_gh_path", AsyncMock(return_value=gh))
    monkeypatch.setattr(
        shell_executor.asyncio,
        "create_subprocess_exec",
        capturing_factory,
    )

    _ = [
        item
        async for item in shell_executor.spawn_shell(
            command="true",
            cwd=Path("."),
            timeout_ms=100,
        )
    ]

    env = captured_kwargs.get("env")
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "ghp_secret"
    assert env["GITHUB_TOKEN"] == "ghp_secret"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert env[AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY]
    assert str(gh.parent) in env["PATH"].split(os.pathsep)


@pytest.mark.asyncio
async def test_spawn_shell_strips_bash_startup_env(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    captured_kwargs: dict[str, object] = {}
    proc = _FakeProcess()

    async def capturing_factory(*args: object, **kwargs: object) -> _FakeProcess:
        captured_kwargs.update(kwargs)

        async def _feed() -> None:
            proc.stdout.feed_eof()
            proc.stderr.feed_eof()
            await asyncio.sleep(0.01)
            proc.returncode = 0
            proc._wait_event.set()

        asyncio.create_task(_feed())
        return proc

    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")
    monkeypatch.setattr(shell_executor, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(
        shell_executor, "_resolve_gh_path", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        shell_executor.asyncio,
        "create_subprocess_exec",
        capturing_factory,
    )
    monkeypatch.setattr(
        shell_executor.os,
        "environ",
        {
            "PATH": "/usr/bin",
            "BASH_ENV": "/tmp/hang.sh",
            "ENV": "/tmp/posix.sh",
            "PROMPT_COMMAND": "sleep 100",
            "PS1": "bad-prompt",
            "BASH_FUNC_module%%": "() { sleep 100; }",
        },
    )

    _ = [
        item
        async for item in shell_executor.spawn_shell(
            command="pwd",
            cwd=Path("."),
            timeout_ms=100,
        )
    ]

    env = captured_kwargs.get("env")
    assert isinstance(env, dict)
    assert "PATH" in env
    assert "BASH_ENV" not in env
    assert "ENV" not in env
    assert "PROMPT_COMMAND" not in env
    assert "PS1" not in env
    assert "BASH_FUNC_module%%" not in env


@pytest.mark.asyncio
async def test_create_shell_subprocess_uses_powershell_wrapper_and_keeps_env(
    monkeypatch,
) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    captured_args: list[object] = []
    captured_kwargs: dict[str, object] = {}

    async def capturing_factory(*args: object, **kwargs: object) -> _FakeProcess:
        captured_args.extend(args)
        captured_kwargs.update(kwargs)
        proc = _FakeProcess()
        proc.stdout.feed_eof()
        proc.stderr.feed_eof()
        proc.returncode = 0
        proc._wait_event.set()
        return proc

    shell = shell_executor.ResolvedShell(
        kind=shell_executor.ShellKind.POWERSHELL,
        executable="powershell.exe",
        display_name="PowerShell",
    )
    monkeypatch.setattr(shell_executor, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(
        shell_executor,
        "_resolve_gh_path",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        shell_executor.asyncio,
        "create_subprocess_exec",
        capturing_factory,
    )
    monkeypatch.setattr(
        shell_executor.os,
        "environ",
        {
            "PATH": r"C:\Windows\System32",
            "BASH_ENV": r"C:\tmp\bashrc",
        },
    )

    _ = await shell_executor.create_shell_subprocess(
        command="Write-Output 'hello'",
        cwd=Path("."),
        shell=shell,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    assert captured_args[0] == "powershell.exe"
    assert captured_args[1] == "-NoProfile"
    assert captured_args[2] == "-Command"
    assert "OutputEncoding" in str(captured_args[3])
    env = captured_kwargs.get("env")
    assert isinstance(env, dict)
    assert env["BASH_ENV"] == r"C:\tmp\bashrc"


def test_run_git_bash_strips_bash_startup_env(monkeypatch, tmp_path: Path) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    captured_kwargs: dict[str, object] = {}

    class _CompletedProcess:
        returncode = 0
        stdout = "/tmp\n"
        stderr = ""

    def fake_run(*args: object, **kwargs: object) -> _CompletedProcess:
        _ = args
        captured_kwargs.update(kwargs)
        return _CompletedProcess()

    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")
    monkeypatch.setattr(shell_executor, "_load_github_cli_env", lambda: {})
    monkeypatch.setattr(shell_executor, "_resolve_gh_path_sync", lambda: None)
    monkeypatch.setattr(shell_executor.subprocess, "run", fake_run)
    monkeypatch.setattr(
        shell_executor.os,
        "environ",
        {
            "PATH": "/usr/bin",
            "BASH_ENV": "/tmp/hang.sh",
            "PROMPT_COMMAND": "sleep 100",
            "BASH_FUNC_module%%": "() { sleep 100; }",
        },
    )

    result = shell_executor.run_git_bash(
        command="pwd",
        workdir=tmp_path,
        timeout_seconds=5,
    )

    env = captured_kwargs.get("env")
    assert isinstance(env, dict)
    assert "BASH_ENV" not in env
    assert "PROMPT_COMMAND" not in env
    assert "BASH_FUNC_module%%" not in env
    assert captured_kwargs.get("stdin") == subprocess.DEVNULL
    assert result == (0, "/tmp\n", "", False)


# ---------------------------------------------------------------------------
# shell_policy: command length
# ---------------------------------------------------------------------------


class TestValidateShellCommand:
    def test_accepts_normal_length_command(self):
        from agent_teams.tools.workspace_tools.shell_policy import (
            validate_shell_command,
        )

        validate_shell_command("echo hello")

    def test_accepts_long_inline_script(self):
        from agent_teams.tools.workspace_tools.shell_policy import (
            validate_shell_command,
        )

        long_cmd = "python -c '" + "x = 1\n" * 500 + "'"
        validate_shell_command(long_cmd)

    def test_rejects_command_above_max_length(self):
        from agent_teams.tools.workspace_tools.shell_policy import (
            MAX_COMMAND_LENGTH,
            validate_shell_command,
        )

        cmd = "echo " + "x" * (MAX_COMMAND_LENGTH + 1)
        with pytest.raises(ValueError, match="too long"):
            validate_shell_command(cmd)

    def test_error_message_includes_length(self):
        from agent_teams.tools.workspace_tools.shell_policy import (
            MAX_COMMAND_LENGTH,
            validate_shell_command,
        )

        cmd = "echo " + "x" * (MAX_COMMAND_LENGTH + 1)
        with pytest.raises(ValueError, match=str(MAX_COMMAND_LENGTH)):
            validate_shell_command(cmd)


def test_run_git_bash_uses_current_proxy_env(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    captured: dict[str, object] = {}
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = args
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            args=["bash"], returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(shell_executor.subprocess, "run", fake_run)

    exit_code, _, _, timed_out = shell_executor.run_git_bash(
        command="echo test",
        workdir=Path("."),
        timeout_seconds=5,
    )

    assert exit_code == 0
    assert timed_out is False
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["HTTP_PROXY"] == "http://proxy.example:8080"


def test_run_git_bash_uses_process_group(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    captured_kwargs: dict[str, object] = {}
    monkeypatch.setattr(shell_executor, "resolve_bash_path", lambda: "bash")

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = args
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            args=["bash"], returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(shell_executor.subprocess, "run", fake_run)

    shell_executor.run_git_bash(
        command="echo test",
        workdir=Path("."),
        timeout_seconds=5,
    )

    if shell_executor._is_windows():
        assert captured_kwargs.get("creationflags", 0) != 0
    else:
        assert captured_kwargs.get("start_new_session") is True
