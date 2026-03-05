# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

from agent_teams.paths import root_paths


def test_get_project_root_returns_git_root(monkeypatch, tmp_path: Path) -> None:
    git_root = tmp_path / "repo"
    git_root.mkdir(parents=True)

    def fake_run(
        command: list[str],
        *,
        cwd: str,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["git", "rev-parse", "--show-toplevel"]
        assert check is False
        assert capture_output is True
        assert text is True
        assert timeout == 5.0
        assert cwd == str(tmp_path.resolve())
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f"{git_root}\n",
            stderr="",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(root_paths.subprocess, "run", fake_run)

    resolved = root_paths.get_project_root()

    assert resolved == git_root.resolve()


def test_get_project_root_falls_back_to_cwd_when_git_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        command: list[str],
        *,
        cwd: str,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (cwd, check, capture_output, text, timeout)
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="fatal: not a git repository",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(root_paths.subprocess, "run", fake_run)

    resolved = root_paths.get_project_root()

    assert resolved == tmp_path.resolve()


def test_get_project_root_passes_start_dir_to_git(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "workspace" / "service"
    project_dir.mkdir(parents=True)
    git_root = tmp_path / "workspace"

    captured: dict[str, str] = {}

    def fake_run(
        command: list[str],
        *,
        cwd: str,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (check, capture_output, text, timeout)
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f"{git_root}\n",
            stderr="",
        )

    monkeypatch.setattr(root_paths.subprocess, "run", fake_run)

    resolved = root_paths.get_project_root(start_dir=project_dir)

    assert captured["cwd"] == str(project_dir.resolve())
    assert resolved == git_root.resolve()


def test_get_project_root_or_none_falls_back_to_cwd_when_git_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        command: list[str],
        *,
        cwd: str,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (command, cwd, check, capture_output, text, timeout)
        return subprocess.CompletedProcess(
            args=["git", "rev-parse", "--show-toplevel"],
            returncode=1,
            stdout="",
            stderr="fatal: not a git repository",
        )

    monkeypatch.setattr(root_paths.subprocess, "run", fake_run)

    assert (
        root_paths.get_project_root_or_none(start_dir=tmp_path) == Path.cwd().resolve()
    )


def test_get_user_home_dir_returns_resolved_home() -> None:
    assert root_paths.get_user_home_dir() == Path.home().resolve()
