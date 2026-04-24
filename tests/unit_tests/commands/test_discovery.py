# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.commands.command_models import (
    CommandDiscoverySource,
    CommandScope,
)
from relay_teams.commands.discovery import discover_commands


def test_discovers_front_matter_and_defaults(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".relay-teams" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "review.md").write_text(
        "---\n"
        "description: Review changes\n"
        "aliases: [review-changes, /review/topic]\n"
        "argument_hint: file path\n"
        "allowed_modes:\n"
        "  - normal\n"
        "---\n"
        "Review {{args}} in {{workspace_root}}",
        encoding="utf-8",
    )

    commands = discover_commands(
        app_config_dir=app_config_dir,
        workspace_root=workspace_root,
    )

    assert len(commands) == 1
    command = commands[0]
    assert command.name == "review"
    assert command.description == "Review changes"
    assert command.aliases == ("review-changes", "review/topic")
    assert command.argument_hint == "file path"
    assert command.allowed_modes == ("normal",)
    assert command.scope == CommandScope.PROJECT
    assert command.discovery_source == CommandDiscoverySource.PROJECT_RELAY_TEAMS


def test_discovers_app_commands_without_workspace(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    command_dir = app_config_dir / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "global.md").write_text(
        "---\nallowed_modes: plan\naliases: global-review, /review/global\n---\nGlobal",
        encoding="utf-8",
    )

    commands = discover_commands(app_config_dir=app_config_dir, workspace_root=None)

    assert len(commands) == 1
    assert commands[0].scope == CommandScope.APP
    assert commands[0].allowed_modes == ("plan",)
    assert commands[0].aliases == ("global-review", "review/global")


def test_skips_invalid_command_files(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".relay-teams" / "commands"
    nested_dir = command_dir / "a" / "b"
    command_dir.mkdir(parents=True)
    nested_dir.mkdir(parents=True)
    (command_dir / "bad name.md").write_text("Bad name", encoding="utf-8")
    (command_dir / "broken.md").write_text(
        "---\ndescription: broken",
        encoding="utf-8",
    )
    (nested_dir / "deep.md").write_text("Too deep", encoding="utf-8")

    commands = discover_commands(
        app_config_dir=app_config_dir,
        workspace_root=workspace_root,
        max_depth=1,
    )

    assert commands == ()


def test_discovers_openspec_claude_colon_command(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".claude" / "commands" / "opsx"
    command_dir.mkdir(parents=True)
    (command_dir / "propose.md").write_text(
        "---\n"
        'name: "OPSX: Propose"\n'
        "description: Propose a change\n"
        "argument-hint: command arguments\n"
        "---\n"
        "Propose a change",
        encoding="utf-8",
    )

    commands = discover_commands(
        app_config_dir=app_config_dir,
        workspace_root=workspace_root,
    )

    assert len(commands) == 1
    command = commands[0]
    assert command.name == "opsx:propose"
    assert command.argument_hint == "command arguments"
    assert command.aliases == ()


def test_discovers_openspec_opencode_alias(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".opencode" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "opsx-propose.md").write_text(
        "---\ndescription: Propose a change\n---\nPropose a change",
        encoding="utf-8",
    )

    commands = discover_commands(
        app_config_dir=app_config_dir,
        workspace_root=workspace_root,
    )

    assert len(commands) == 1
    command = commands[0]
    assert command.name == "opsx-propose"
    assert command.aliases == ("opsx:propose",)


def test_discovers_nested_command_name(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    command_dir = workspace_root / ".codex" / "commands" / "review"
    command_dir.mkdir(parents=True)
    (command_dir / "security.md").write_text("Audit {{args}}", encoding="utf-8")

    commands = discover_commands(
        app_config_dir=app_config_dir,
        workspace_root=workspace_root,
    )

    assert [command.name for command in commands] == ["review/security"]
