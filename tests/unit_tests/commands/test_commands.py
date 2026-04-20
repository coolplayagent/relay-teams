# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.commands.command_models import CommandScope
from relay_teams.commands.discovery import CommandsDirectory
from relay_teams.commands.registry import CommandRegistry
from relay_teams.commands.resolver import CommandResolver


@pytest.fixture()
def tmp_command_dirs(tmp_path: Path) -> tuple[Path, Path]:
    app_dir = tmp_path / "app_commands"
    project_dir = tmp_path / "project_commands"
    app_dir.mkdir()
    project_dir.mkdir()
    return app_dir, project_dir


def _write_command(directory: Path, name: str, content: str) -> Path:
    path = directory / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


class TestDiscovery:
    def test_discovers_command_without_front_matter(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, project_dir = tmp_command_dirs
        _write_command(app_dir, "hello", "Say hello to the user.")

        directory = CommandsDirectory(app_commands_dir=app_dir)
        directory.discover()
        commands = directory.list_commands()

        assert len(commands) == 1
        cmd = commands[0]
        assert cmd.name == "hello"
        assert cmd.body == "Say hello to the user."
        assert cmd.scope == CommandScope.APP

    def test_discovers_command_with_front_matter(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        _write_command(
            app_dir,
            "review",
            "---\nname: review\ndescription: Review PR\nargument_hint: <pr_number>\nallowed_modes: [normal]\n---\nReview PR {{args}}.",
        )

        directory = CommandsDirectory(app_commands_dir=app_dir)
        directory.discover()
        commands = directory.list_commands()

        assert len(commands) == 1
        cmd = commands[0]
        assert cmd.name == "review"
        assert cmd.description == "Review PR"
        assert cmd.argument_hint == "<pr_number>"
        assert cmd.allowed_modes == ["normal"]
        assert cmd.body == "Review PR {{args}}."

    def test_project_takes_priority_over_app(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, project_dir = tmp_command_dirs
        _write_command(app_dir, "deploy", "---\nname: deploy\n---\nApp deploy.")
        _write_command(project_dir, "deploy", "---\nname: deploy\n---\nProject deploy.")

        directory = CommandsDirectory(
            app_commands_dir=app_dir,
            project_commands_dir=project_dir,
        )
        directory.discover()
        cmd = directory.get_command("deploy")

        assert cmd is not None
        assert cmd.scope == CommandScope.PROJECT
        assert cmd.body == "Project deploy."

    def test_ignores_non_markdown_files(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        (app_dir / "notes.txt").write_text("not a command", encoding="utf-8")
        (app_dir / "readme").write_text("also not", encoding="utf-8")

        directory = CommandsDirectory(app_commands_dir=app_dir)
        directory.discover()
        assert directory.list_commands() == []

    def test_empty_directory(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        directory = CommandsDirectory(app_commands_dir=app_dir)
        directory.discover()
        assert directory.list_commands() == []


class TestRegistry:
    def test_list_summaries(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        _write_command(
            app_dir,
            "test",
            "---\nname: test\ndescription: Run tests\n---\nRun tests.",
        )

        registry = CommandRegistry(
            directory=CommandsDirectory(app_commands_dir=app_dir)
        )
        summaries = registry.list_summaries()

        assert len(summaries) == 1
        assert summaries[0].name == "test"
        assert summaries[0].description == "Run tests"


class TestResolver:
    def test_resolves_command_with_args(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        _write_command(
            app_dir,
            "review",
            "---\nname: review\n---\nPlease review PR #{{args}} in {{workspace_root}}.",
        )

        registry = CommandRegistry(
            directory=CommandsDirectory(app_commands_dir=app_dir)
        )
        resolver = CommandResolver(registry=registry, workspace_root=Path("/workspace"))

        result = resolver.resolve("/review 42")

        assert result.command_name == "review"
        assert result.args == "42"
        assert "PR #42" in result.expanded_prompt
        assert "/workspace" in result.expanded_prompt
        assert result.prompt_length > 0

    def test_resolves_command_without_args(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        _write_command(app_dir, "status", "---\nname: status\n---\nShow current status.")

        registry = CommandRegistry(
            directory=CommandsDirectory(app_commands_dir=app_dir)
        )
        resolver = CommandResolver(registry=registry)

        result = resolver.resolve("/status")

        assert result.command_name == "status"
        assert result.args == ""
        assert result.expanded_prompt == "Show current status."

    def test_raises_for_unknown_command(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        registry = CommandRegistry(
            directory=CommandsDirectory(app_commands_dir=app_dir)
        )
        resolver = CommandResolver(registry=registry)

        with pytest.raises(KeyError):
            resolver.resolve("/nonexistent")

    def test_raises_for_wrong_mode(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        _write_command(
            app_dir,
            "orch",
            "---\nname: orch\nallowed_modes: [orchestration]\n---\nOrchestrate.",
        )

        registry = CommandRegistry(
            directory=CommandsDirectory(app_commands_dir=app_dir)
        )
        resolver = CommandResolver(registry=registry)

        with pytest.raises(ValueError):
            resolver.resolve("/orch", mode="normal")

    def test_non_slash_text_returns_none(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        _write_command(app_dir, "test", "---\nname: test\n---\nTest.")

        registry = CommandRegistry(
            directory=CommandsDirectory(app_commands_dir=app_dir)
        )
        resolver = CommandResolver(registry=registry)

        assert resolver.try_resolve("hello world") is None

    def test_template_variables(self, tmp_command_dirs: tuple[Path, Path]) -> None:
        app_dir, _ = tmp_command_dirs
        _write_command(
            app_dir,
            "cwd-cmd",
            "---\nname: cwd-cmd\n---\nWorkspace: {{workspace_root}}, cwd: {{cwd}}, args: {{args}}.",
        )

        registry = CommandRegistry(
            directory=CommandsDirectory(app_commands_dir=app_dir)
        )
        resolver = CommandResolver(registry=registry, workspace_root=Path("/ws"))

        result = resolver.resolve("/cwd-cmd foo bar")

        assert "/ws" in result.expanded_prompt
        assert "foo bar" in result.expanded_prompt
