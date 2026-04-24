# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.commands import CommandModeNotAllowed, CommandRegistry


def test_project_command_overrides_app_command(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    (app_config_dir / "commands").mkdir(parents=True)
    (workspace_root / ".relay-teams" / "commands").mkdir(parents=True)
    (app_config_dir / "commands" / "review.md").write_text(
        "App review",
        encoding="utf-8",
    )
    (workspace_root / ".relay-teams" / "commands" / "review.md").write_text(
        "Project review",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    command = registry.get_command("review", workspace_root=workspace_root)

    assert command is not None
    assert command.template == "Project review"


def test_lists_app_and_project_commands(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    (app_config_dir / "commands").mkdir(parents=True)
    (workspace_root / ".relay-teams" / "commands").mkdir(parents=True)
    (app_config_dir / "commands" / "global.md").write_text(
        "Global",
        encoding="utf-8",
    )
    (workspace_root / ".relay-teams" / "commands" / "local.md").write_text(
        "Local",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    assert [command.name for command in registry.list_app_commands()] == ["global"]
    assert registry.list_project_commands(workspace_root=None) == ()
    assert [
        command.name
        for command in registry.list_project_commands(workspace_root=workspace_root)
    ] == ["local"]


def test_duplicate_alias_does_not_override_real_command(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    command_dir = app_config_dir / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "alias-source.md").write_text(
        "---\naliases: [target]\n---\nAlias source",
        encoding="utf-8",
    )
    (command_dir / "target.md").write_text("Target command", encoding="utf-8")
    registry = CommandRegistry(app_config_dir=app_config_dir)

    command = registry.get_command("target", workspace_root=None)

    assert command is not None
    assert command.name == "target"
    assert command.template == "Target command"


def test_duplicate_alias_uses_last_discovered_command(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    command_dir = app_config_dir / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "first.md").write_text(
        "---\naliases: [shared]\n---\nFirst",
        encoding="utf-8",
    )
    (command_dir / "second.md").write_text(
        "---\naliases: [shared]\n---\nSecond",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    result = registry.resolve(
        raw_text="/shared value",
        mode="normal",
        workspace_root=None,
        cwd=None,
    )

    assert result.matched is True
    assert result.resolved_name == "second"
    assert result.expanded_prompt == "Second\n\nvalue"


def test_project_alias_overrides_app_alias_in_workspace(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    (app_config_dir / "commands").mkdir(parents=True)
    (workspace_root / ".relay-teams" / "commands").mkdir(parents=True)
    (app_config_dir / "commands" / "global.md").write_text(
        "---\nname: global\naliases: [shared]\n---\nGlobal",
        encoding="utf-8",
    )
    (workspace_root / ".relay-teams" / "commands" / "project.md").write_text(
        "---\nname: project\naliases: [shared]\n---\nProject",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    result = registry.resolve(
        raw_text="/shared value",
        mode="normal",
        workspace_root=workspace_root,
        cwd=workspace_root,
    )

    assert result.matched is True
    assert result.resolved_name == "project"
    assert result.expanded_prompt == "Project\n\nvalue"


def test_shadowed_command_aliases_follow_latest_discovery_order(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    (app_config_dir / "commands").mkdir(parents=True)
    (workspace_root / ".relay-teams" / "commands").mkdir(parents=True)
    (app_config_dir / "commands" / "base.md").write_text(
        "---\nname: base\naliases: [shared]\n---\nApp base",
        encoding="utf-8",
    )
    (app_config_dir / "commands" / "other.md").write_text(
        "---\nname: other\naliases: [shared]\n---\nOther",
        encoding="utf-8",
    )
    (workspace_root / ".relay-teams" / "commands" / "base.md").write_text(
        "---\nname: base\naliases: [shared]\n---\nProject base",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    result = registry.resolve(
        raw_text="/shared value",
        mode="normal",
        workspace_root=workspace_root,
        cwd=workspace_root,
    )

    assert result.matched is True
    assert result.resolved_name == "base"
    assert result.expanded_prompt == "Project base\n\nvalue"


def test_resolve_replaces_template_variables(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".relay-teams" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "review.md").write_text(
        "Review {{args}} from {{cwd}} in {{workspace_root}}",
        encoding="utf-8",
    )
    cwd = workspace_root / "subdir"
    cwd.mkdir()
    registry = CommandRegistry(app_config_dir=app_config_dir)

    result = registry.resolve(
        raw_text="/review file.py",
        mode="normal",
        workspace_root=workspace_root,
        cwd=cwd,
    )

    assert result.matched is True
    assert result.parsed_name == "review"
    assert result.resolved_name == "review"
    assert result.expanded_prompt == (
        f"Review file.py from {cwd.resolve()} in {workspace_root.resolve()}"
    )


def test_resolve_supports_dollar_arguments_placeholder(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".claude" / "commands" / "opsx"
    command_dir.mkdir(parents=True)
    (command_dir / "apply.md").write_text(
        "Apply change $ARGUMENTS",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    result = registry.resolve(
        raw_text="/opsx:apply add-login",
        mode="normal",
        workspace_root=workspace_root,
        cwd=workspace_root,
    )

    assert result.matched is True
    assert result.expanded_prompt == "Apply change add-login"


def test_resolve_appends_args_when_template_has_no_placeholder(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".opencode" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "opsx-propose.md").write_text(
        "Propose a change",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    result = registry.resolve(
        raw_text="/opsx:propose add-login",
        mode="normal",
        workspace_root=workspace_root,
        cwd=workspace_root,
    )

    assert result.matched is True
    assert result.resolved_name == "opsx-propose"
    assert result.expanded_prompt == "Propose a change\n\nadd-login"


def test_resolve_unknown_slash_passthrough(tmp_path: Path) -> None:
    registry = CommandRegistry(app_config_dir=tmp_path / "app")

    result = registry.resolve(
        raw_text="/unknown keep this",
        mode="normal",
        workspace_root=tmp_path,
        cwd=tmp_path,
    )

    assert result.matched is False
    assert result.parsed_name == "unknown"
    assert result.expanded_prompt is None


def test_resolve_non_slash_passthrough(tmp_path: Path) -> None:
    registry = CommandRegistry(app_config_dir=tmp_path / "app")

    result = registry.resolve(
        raw_text="plain text",
        mode="normal",
        workspace_root=None,
        cwd=None,
    )

    assert result.matched is False
    assert result.parsed_name is None
    assert result.args == ""


def test_resolve_blank_mode_defaults_to_normal(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    command_dir = app_config_dir / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "review.md").write_text(
        "---\nallowed_modes: [normal]\n---\nReview {{cwd}}",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    result = registry.resolve(
        raw_text="/review",
        mode="",
        workspace_root=tmp_path,
        cwd=None,
    )

    assert result.matched is True
    assert result.expanded_prompt == f"Review {tmp_path.resolve()}"


def test_resolve_rejects_disallowed_mode(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".relay-teams" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "normal-only.md").write_text(
        "---\nallowed_modes: [normal]\n---\nRun",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)

    with pytest.raises(CommandModeNotAllowed):
        registry.resolve(
            raw_text="/normal-only",
            mode="orchestration",
            workspace_root=workspace_root,
            cwd=workspace_root,
        )
