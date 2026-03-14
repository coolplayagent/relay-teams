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
        assert result == 30000

    def test_custom_timeout(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        result = normalize_timeout(60000)
        assert result == 60000

    def test_timeout_too_large(self):
        from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout

        result = normalize_timeout(200000)
        assert result == 120000

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


@pytest.mark.asyncio
async def test_spawn_shell_does_not_timeout_after_streams_finish(monkeypatch) -> None:
    from agent_teams.tools.workspace_tools import shell_executor

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.returncode: int | None = None
            self._wait_event = asyncio.Event()

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

    proc = _FakeProcess()

    async def fake_create_subprocess_exec(
        *args: object, **kwargs: object
    ) -> _FakeProcess:
        _ = (args, kwargs)

        async def _feed() -> None:
            proc.stdout.feed_data(b"/d/workspace/aider\n")
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
        fake_create_subprocess_exec,
    )

    chunks = [
        item
        async for item in shell_executor.spawn_shell(
            command="pwd",
            cwd=Path("."),
            timeout_ms=100,
        )
    ]

    assert chunks == [("stdout", "/d/workspace/aider\n")]
    assert proc.returncode == 0


def test_format_timeout_metadata_uses_normalized_timeout() -> None:
    from agent_teams.tools.workspace_tools.shell import _format_timeout_metadata

    metadata = _format_timeout_metadata(30000)

    assert "30000ms" in metadata
    assert "Nonems" not in metadata


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
