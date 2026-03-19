# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess

import pytest


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


# ---------------------------------------------------------------------------
# _save_overflow_output
# ---------------------------------------------------------------------------


class TestSaveOverflowOutput:
    def test_returns_none_when_within_limit(self, tmp_path: Path) -> None:
        from agent_teams.tools.workspace_tools.shell import (
            MAX_OUTPUT_CHARS,
            _save_overflow_output,
        )

        workspace = self._make_workspace(tmp_path)
        content = "x" * MAX_OUTPUT_CHARS
        assert _save_overflow_output(workspace, content, "stdout") is None

    def test_creates_file_when_exceeding_limit(self, tmp_path: Path) -> None:
        from agent_teams.tools.workspace_tools.shell import (
            MAX_OUTPUT_CHARS,
            _save_overflow_output,
        )

        workspace = self._make_workspace(tmp_path)
        content = "x" * (MAX_OUTPUT_CHARS + 100)
        result = _save_overflow_output(workspace, content, "stdout")
        assert result is not None
        assert result.exists()
        assert result.read_text(encoding="utf-8") == content
        assert "shell_output" in str(result)

    @staticmethod
    def _make_workspace(tmp_path: Path):
        """Build a minimal WorkspaceHandle-like object for testing."""
        from unittest.mock import MagicMock

        workspace = MagicMock()
        workspace.locations.workspace_dir = tmp_path
        return workspace


# ---------------------------------------------------------------------------
# Existing helpers
# ---------------------------------------------------------------------------


def test_format_timeout_metadata_uses_normalized_timeout() -> None:
    from agent_teams.tools.workspace_tools.shell import _format_timeout_metadata

    metadata = _format_timeout_metadata(30000)

    assert "30000ms" in metadata
    assert "Nonems" not in metadata


def test_project_shell_result_hides_raw_streams_from_visible_payload(
    tmp_path: Path,
) -> None:
    from agent_teams.tools.workspace_tools.shell import _project_shell_result

    stdout_path = tmp_path / "stdout.txt"
    result = _project_shell_result(
        exit_code=1,
        timed_out=False,
        stdout="stdout text",
        stderr="stderr text",
        output="combined output",
        stdout_overflow=stdout_path,
        stderr_overflow=None,
    )

    assert result.visible_data == {
        "output": "combined output",
        "exit_code": 1,
        "timed_out": False,
        "truncated": True,
    }
    assert result.internal_data == {
        "exit_code": 1,
        "timed_out": False,
        "stdout": "stdout text",
        "stderr": "stderr text",
        "output": "combined output",
        "stdout_overflow_path": str(stdout_path),
    }


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
