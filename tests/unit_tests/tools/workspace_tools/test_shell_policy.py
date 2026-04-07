# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.sessions.runs.background_tasks.command_runtime import (
    CommandRuntimeKind,
    ResolvedCommandRuntime,
)
from agent_teams.tools.workspace_tools import shell_policy as shell_policy_module
from agent_teams.tools.workspace_tools.shell_policy import (
    MAX_TIMEOUT_SECONDS,
    ShellRuntimeFamily,
    normalize_timeout,
    validate_shell_command,
)


def _mock_powershell_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shell_policy_module,
        "resolve_command_runtime",
        lambda *, command=None: ResolvedCommandRuntime(
            kind=CommandRuntimeKind.POWERSHELL,
            executable="powershell.exe",
            display_name="PowerShell",
        ),
    )


def test_shell_policy_allows_python_inline_command() -> None:
    decision = validate_shell_command('python -c "print(1)"')

    assert decision.normalized_command == 'python -c "print(1)"'
    assert decision.prefix_candidates == ("python",)


def test_shell_policy_rejects_empty_command() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        validate_shell_command("   ")


def test_shell_policy_rejects_banned_bash_download_command() -> None:
    with pytest.raises(ValueError, match="curl"):
        validate_shell_command("curl https://example.com")


def test_shell_policy_rejects_banned_bash_download_exe_command() -> None:
    with pytest.raises(ValueError, match="curl\\.exe"):
        validate_shell_command("curl.exe https://example.com")


def test_shell_policy_rejects_banned_bash_download_env_wrapper() -> None:
    with pytest.raises(ValueError, match="curl"):
        validate_shell_command("env curl https://example.com")


def test_shell_policy_rejects_banned_bash_download_path_qualified_env_wrapper() -> None:
    with pytest.raises(ValueError, match="curl"):
        validate_shell_command("/usr/bin/env curl https://example.com")


def test_shell_policy_rejects_banned_bash_download_command_wrapper() -> None:
    with pytest.raises(ValueError, match="curl"):
        validate_shell_command("command curl https://example.com")


def test_shell_policy_rejects_banned_bash_download_noglob_wrapper() -> None:
    with pytest.raises(ValueError, match="curl"):
        validate_shell_command("noglob curl https://example.com")


def test_shell_policy_rejects_banned_bash_download_in_dollar_substitution() -> None:
    with pytest.raises(ValueError, match="curl"):
        validate_shell_command("echo $(curl https://example.com)")


def test_shell_policy_rejects_banned_bash_download_in_backtick_substitution() -> None:
    with pytest.raises(ValueError, match="curl"):
        validate_shell_command("echo `curl https://example.com`")


def test_shell_policy_rejects_powershell_download_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_powershell_runtime(monkeypatch)
    with pytest.raises(ValueError, match="iwr"):
        validate_shell_command("iwr https://example.com")


def test_shell_policy_tracks_git_prefixes_across_compound_command() -> None:
    decision = validate_shell_command("git status && git diff --stat")

    assert decision.runtime_family in {
        ShellRuntimeFamily.BASH,
        ShellRuntimeFamily.GIT_BASH,
    }
    assert decision.prefix_candidates == ("git status", "git diff")


def test_shell_policy_rejects_banned_bash_command_after_single_ampersand() -> None:
    with pytest.raises(ValueError, match="curl"):
        validate_shell_command("echo ok & curl https://example.com")


def test_shell_policy_tracks_direct_powershell_script_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_powershell_runtime(monkeypatch)
    decision = validate_shell_command("& 'C:\\Tools\\job.ps1' -Flag value")

    assert decision.runtime_family == ShellRuntimeFamily.POWERSHELL
    assert decision.prefix_candidates == ("job.ps1",)


def test_shell_policy_yolo_rejects_parent_directory_change_for_bash(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()
    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "cd .. && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_allows_child_directory_change_for_bash(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()
    decision = validate_shell_command(
        "cd tests && pytest",
        yolo=True,
        effective_cwd=cwd,
    )

    assert decision.prefix_candidates == ("cd", "pytest")


def test_shell_policy_yolo_rejects_parent_directory_change_for_bash_cd_dash_dash(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()
    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "cd -- .. && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_parent_directory_change_for_command_wrapped_bash_cd(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()
    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "command cd .. && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_parent_directory_change_for_bash_cd_dash_p(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()
    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "cd -P .. && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_parent_directory_change_for_bash_pushd(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()
    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "pushd .. && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_home_directory_shorthand_for_bash(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()

    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "cd ~ && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_oldpwd_shorthand_for_bash(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()

    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "cd - && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_env_var_directory_change_for_bash(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()

    with pytest.raises(ValueError, match="requires shell expansion"):
        validate_shell_command(
            "cd $HOME && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_cdpath_directory_change_for_bash(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()

    with pytest.raises(ValueError, match="CDPATH requires shell expansion"):
        validate_shell_command(
            "CDPATH=/ cd tmp && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_braced_env_var_directory_change_for_bash(
    tmp_path: Path,
) -> None:
    cwd = (tmp_path / "project").resolve()

    with pytest.raises(ValueError, match="requires shell expansion"):
        validate_shell_command(
            "cd ${OLDPWD} && pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_strips_leading_noop_cd_prefix(tmp_path: Path) -> None:
    cwd = (tmp_path / "project").resolve()

    decision = validate_shell_command(
        f"cd {cwd} && git status",
        yolo=True,
        effective_cwd=cwd,
    )

    assert decision.subcommands == ("git status",)
    assert decision.prefix_candidates == ("git status",)


def test_shell_policy_approval_mode_keeps_parent_directory_change_allowed() -> None:
    decision = validate_shell_command("cd .. && pwd")

    assert decision.prefix_candidates == ("cd", "pwd")


def test_shell_policy_yolo_rejects_parent_directory_change_for_powershell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_powershell_runtime(monkeypatch)
    cwd = (tmp_path / "project").resolve()
    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "Set-Location ..; pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_allows_child_directory_change_for_powershell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_powershell_runtime(monkeypatch)
    cwd = (tmp_path / "project").resolve()
    decision = validate_shell_command(
        "Set-Location -Path tests; pytest",
        yolo=True,
        effective_cwd=cwd,
    )

    assert decision.runtime_family == ShellRuntimeFamily.POWERSHELL
    assert decision.prefix_candidates == ("set-location", "pytest")


def test_shell_policy_yolo_rejects_parent_directory_change_for_powershell_pushd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_powershell_runtime(monkeypatch)
    cwd = (tmp_path / "project").resolve()
    with pytest.raises(ValueError, match="directory change is blocked"):
        validate_shell_command(
            "pushd ..; pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_home_directory_change_for_powershell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_powershell_runtime(monkeypatch)
    cwd = (tmp_path / "project").resolve()

    with pytest.raises(ValueError, match="requires shell expansion"):
        validate_shell_command(
            "Set-Location $HOME; pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_policy_yolo_rejects_env_directory_change_for_powershell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_powershell_runtime(monkeypatch)
    cwd = (tmp_path / "project").resolve()

    with pytest.raises(ValueError, match="requires shell expansion"):
        validate_shell_command(
            "Set-Location $env:USERPROFILE; pytest",
            yolo=True,
            effective_cwd=cwd,
        )


def test_shell_timeout_normalization() -> None:
    assert normalize_timeout(None) > 0
    assert normalize_timeout(MAX_TIMEOUT_SECONDS + 99) == MAX_TIMEOUT_SECONDS
