# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

from relay_teams.paths import root_paths


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


def test_get_project_config_dir_uses_project_root_when_available(
    monkeypatch,
    tmp_path: Path,
) -> None:
    user_home_dir = tmp_path / "home"
    monkeypatch.delenv("RELAY_TEAMS_CONFIG_DIR", raising=False)
    monkeypatch.setattr(root_paths, "get_user_home_dir", lambda: user_home_dir)

    config_dir = root_paths.get_project_config_dir()

    assert config_dir == user_home_dir / ".relay-teams"


def test_get_project_config_dir_falls_back_to_cwd_when_git_root_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    user_home_dir = tmp_path / "home"
    monkeypatch.delenv("RELAY_TEAMS_CONFIG_DIR", raising=False)
    monkeypatch.setattr(root_paths, "get_user_home_dir", lambda: user_home_dir)

    config_dir = root_paths.get_project_config_dir()

    assert config_dir == user_home_dir / ".relay-teams"


def test_get_project_config_dir_prefers_cwd_local_config_over_git_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    user_home_dir = tmp_path / "home"
    monkeypatch.delenv("RELAY_TEAMS_CONFIG_DIR", raising=False)
    monkeypatch.setattr(root_paths, "get_user_home_dir", lambda: user_home_dir)

    config_dir = root_paths.get_project_config_dir()

    assert config_dir == user_home_dir / ".relay-teams"


def test_get_project_config_dir_uses_project_root_override() -> None:
    user_home_dir = Path("D:/home-root").resolve()

    config_dir = root_paths.get_project_config_dir(project_root=user_home_dir)

    assert config_dir == Path.home().resolve() / ".relay-teams"


def test_get_user_home_dir_returns_resolved_home() -> None:
    assert root_paths.get_user_home_dir() == Path.home().resolve()


def test_get_user_config_dir_uses_resolved_home(monkeypatch, tmp_path: Path) -> None:
    user_home_dir = tmp_path / "home"
    monkeypatch.delenv("RELAY_TEAMS_CONFIG_DIR", raising=False)
    monkeypatch.setattr(root_paths, "get_user_home_dir", lambda: user_home_dir)

    config_dir = root_paths.get_user_config_dir()

    assert config_dir == user_home_dir / ".relay-teams"


def test_get_user_config_dir_uses_user_home_override(tmp_path: Path) -> None:
    user_home_dir = tmp_path / "home"

    config_dir = root_paths.get_user_config_dir(user_home_dir=user_home_dir)

    assert config_dir == user_home_dir.resolve() / ".relay-teams"


def test_get_project_config_dir_resolves_user_supplied_root(tmp_path: Path) -> None:
    unresolved_project_root = tmp_path / "parent" / ".." / "project-root"
    unresolved_project_root.mkdir(parents=True)

    config_dir = root_paths.get_project_config_dir(project_root=unresolved_project_root)

    assert config_dir == Path.home().resolve() / ".relay-teams"


def test_get_app_config_dir_prefers_environment_variable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_dir = tmp_path / "runtime-config"
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(configured_dir))

    assert root_paths.get_app_config_dir() == configured_dir.resolve()


def test_get_app_config_dir_ignores_blank_environment_variable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    user_home_dir = tmp_path / "home"
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", "   ")
    monkeypatch.setattr(root_paths, "get_user_home_dir", lambda: user_home_dir)

    assert root_paths.get_app_config_dir() == user_home_dir / ".relay-teams"


def test_get_app_config_dir_expands_tilde_environment_variable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    user_home_dir = tmp_path / "home"
    user_home_dir.mkdir(parents=True)
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", "~/custom-config")
    monkeypatch.setenv("HOME", str(user_home_dir))
    monkeypatch.setenv("USERPROFILE", str(user_home_dir))
    monkeypatch.setattr(root_paths, "get_user_home_dir", lambda: user_home_dir)

    assert (
        root_paths.get_app_config_dir() == (user_home_dir / "custom-config").resolve()
    )


def test_get_app_config_dir_override_returns_resolved_environment_value(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_dir = tmp_path / "override-config"
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(configured_dir))

    assert root_paths.get_app_config_dir_override() == configured_dir.resolve()


def test_get_app_bin_dir_uses_app_config_dir_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_dir = tmp_path / "override-config"
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(configured_dir))

    assert root_paths.get_app_bin_dir() == configured_dir.resolve() / "bin"


def test_get_app_config_file_path_uses_explicit_config_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"

    assert root_paths.get_app_config_file_path("model.json", config_dir=config_dir) == (
        config_dir.resolve() / "model.json"
    )


def test_format_app_config_file_reference_uses_file_path_without_env_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("RELAY_TEAMS_CONFIG_DIR", raising=False)
    config_dir = tmp_path / "config"

    assert (
        root_paths.format_app_config_file_reference(
            "model.json",
            config_dir=config_dir,
        )
        == f'"{config_dir.resolve() / "model.json"}"'
    )


def test_format_app_config_file_reference_uses_file_path_with_env_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_dir = tmp_path / "config"
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", str(configured_dir))

    assert (
        root_paths.format_app_config_file_reference("model.json")
        == f'"{configured_dir.resolve() / "model.json"}"'
    )


def test_format_app_config_file_reference_preserves_tilde_env_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    user_home_dir = tmp_path / "home"
    user_home_dir.mkdir(parents=True)
    monkeypatch.setenv("RELAY_TEAMS_CONFIG_DIR", "~/custom-config")
    monkeypatch.setenv("HOME", str(user_home_dir))
    monkeypatch.setenv("USERPROFILE", str(user_home_dir))

    assert (
        root_paths.format_app_config_file_reference("model.json")
        == f'"{(user_home_dir / "custom-config" / "model.json").resolve()}"'
    )


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
