# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

from agent_teams.paths import root_paths


def test_get_project_root_or_none_returns_git_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
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

    resolved = root_paths.get_project_root_or_none()

    assert resolved == git_root.resolve()


def test_get_project_root_or_none_returns_none_when_git_fails(
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

    resolved = root_paths.get_project_root_or_none()

    assert resolved is None


def test_get_project_root_or_none_passes_start_dir_to_git(
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

    resolved = root_paths.get_project_root_or_none(start_dir=project_dir)

    assert captured["cwd"] == str(project_dir.resolve())
    assert resolved == git_root.resolve()


def test_get_project_root_or_none_returns_none_on_git_failure(
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

    assert root_paths.get_project_root_or_none(start_dir=tmp_path) is None


def test_get_project_root_or_none_returns_none_on_os_error(
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
        raise OSError("git unavailable")

    monkeypatch.setattr(root_paths.subprocess, "run", fake_run)

    assert root_paths.get_project_root_or_none(start_dir=tmp_path) is None


def test_get_project_root_or_none_returns_none_on_timeout(
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
        raise subprocess.TimeoutExpired(cmd="git", timeout=5.0)

    monkeypatch.setattr(root_paths.subprocess, "run", fake_run)

    assert root_paths.get_project_root_or_none(start_dir=tmp_path) is None


def test_get_project_root_or_none_returns_none_on_blank_stdout(
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
            returncode=0,
            stdout="\n\n",
            stderr="",
        )

    monkeypatch.setattr(root_paths.subprocess, "run", fake_run)

    assert root_paths.get_project_root_or_none(start_dir=tmp_path) is None


def test_get_project_config_dir_uses_project_root_when_available(monkeypatch) -> None:
    project_root = Path("D:/repo-root").resolve()
    monkeypatch.setattr(root_paths, "get_project_root_or_none", lambda: project_root)

    config_dir = root_paths.get_project_config_dir()

    assert config_dir == project_root / ".agent_teams"


def test_get_project_config_dir_falls_back_to_cwd_when_git_root_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(root_paths, "get_project_root_or_none", lambda: None)

    config_dir = root_paths.get_project_config_dir()

    assert config_dir == tmp_path.resolve() / ".agent_teams"


def test_get_project_config_dir_uses_project_root_override() -> None:
    project_root = Path("D:/repo-root").resolve()

    config_dir = root_paths.get_project_config_dir(project_root=project_root)

    assert config_dir == project_root / ".agent_teams"


def test_get_user_home_dir_returns_resolved_home() -> None:
    assert root_paths.get_user_home_dir() == Path.home().resolve()


def test_get_user_config_dir_uses_resolved_home(monkeypatch, tmp_path: Path) -> None:
    user_home_dir = tmp_path / "home"
    monkeypatch.setattr(root_paths, "get_user_home_dir", lambda: user_home_dir)

    config_dir = root_paths.get_user_config_dir()

    assert config_dir == user_home_dir / ".agent_teams"


def test_get_user_config_dir_uses_user_home_override(tmp_path: Path) -> None:
    user_home_dir = tmp_path / "home"

    config_dir = root_paths.get_user_config_dir(user_home_dir=user_home_dir)

    assert config_dir == user_home_dir.resolve() / ".agent_teams"


def test_get_project_config_dir_resolves_user_supplied_root(tmp_path: Path) -> None:
    unresolved_project_root = tmp_path / "parent" / ".." / "project-root"
    unresolved_project_root.mkdir(parents=True)

    config_dir = root_paths.get_project_config_dir(project_root=unresolved_project_root)

    assert config_dir == (tmp_path / "project-root").resolve() / ".agent_teams"




def test_resolve_start_dir_defaults_to_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    resolved = root_paths._resolve_start_dir(start_dir=None)

    assert resolved == tmp_path.resolve()


def test_resolve_start_dir_keeps_directory_input(tmp_path: Path) -> None:
    nested_dir = tmp_path / "nested-dir"
    nested_dir.mkdir()

    resolved = root_paths._resolve_start_dir(start_dir=nested_dir)

    assert resolved == nested_dir.resolve()

def test_resolve_start_dir_uses_parent_for_file_path(tmp_path: Path) -> None:
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    file_path = nested_dir / "source.txt"
    file_path.write_text("data", encoding="utf-8")

    resolved = root_paths._resolve_start_dir(start_dir=file_path)

    assert resolved == nested_dir.resolve()
