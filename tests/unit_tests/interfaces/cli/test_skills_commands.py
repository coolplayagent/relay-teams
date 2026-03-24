# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app
from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.skills.skill_registry import SkillRegistry

runner = CliRunner()


def test_skills_list_prefers_app_skill_in_json_output(
    tmp_path: Path, monkeypatch
) -> None:
    registry = _build_registry(tmp_path)
    monkeypatch.setattr(
        "agent_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(cli_app.app, ["skills", "list", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == [
        {
            "name": "app_only",
            "source": "app",
            "directory": (tmp_path / ".config" / "agent-teams" / "skills" / "app_only")
            .resolve()
            .as_posix(),
            "description": "app only skill",
        },
        {
            "name": "builtin_only",
            "source": "builtin",
            "directory": (tmp_path / "builtin" / "skills" / "builtin_only")
            .resolve()
            .as_posix(),
            "description": "builtin only skill",
        },
        {
            "name": "shared",
            "source": "app",
            "directory": (tmp_path / ".config" / "agent-teams" / "skills" / "shared")
            .resolve()
            .as_posix(),
            "description": "app shared skill",
        },
    ]


def test_skills_show_returns_effective_skill_details(
    tmp_path: Path, monkeypatch
) -> None:
    registry = _build_registry(tmp_path)
    monkeypatch.setattr(
        "agent_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(
        cli_app.app, ["skills", "show", "shared", "--format", "json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["name"] == "shared"
    assert payload["source"] == "app"
    assert payload["description"] == "app shared skill"
    assert (
        payload["manifest_path"]
        == (tmp_path / ".config" / "agent-teams" / "skills" / "shared" / "SKILL.md")
        .resolve()
        .as_posix()
    )
    assert payload["instructions"] == "App instructions."
    assert payload["files"] == [
        (tmp_path / ".config" / "agent-teams" / "skills" / "shared" / "SKILL.md")
        .resolve()
        .as_posix()
    ]


def test_skills_list_table_output_is_rendered(tmp_path: Path, monkeypatch) -> None:
    registry = _build_registry(tmp_path)
    monkeypatch.setattr(
        "agent_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(cli_app.app, ["skills", "list"])

    assert result.exit_code == 0
    assert result.output.startswith("Skills (3 total)")
    assert "| Name" in result.output
    assert "shared" in result.output
    assert "app" in result.output


def test_skills_help_explains_merge_order() -> None:
    result = runner.invoke(cli_app.app, ["skills", "--help"])

    assert result.exit_code == 0
    assert (
        "Inspect skills discovered from built-in defaults and the app directory."
        in result.output
    )
    assert "~/.config/agent-teams/skills" in result.output
    assert "app scope, overrides builtin skills" in result.output
    assert "agent-teams skills show time" in result.output


def test_skills_list_help_includes_examples_and_source_behavior() -> None:
    result = runner.invoke(cli_app.app, ["skills", "list", "--help"])

    assert result.exit_code == 0
    assert (
        "List effective skills after merging builtin and app scopes." in result.output
    )
    assert (
        "If the same skill exists in both places, the app copy is shown."
        in result.output
    )
    assert "--source" in result.output
    assert "agent-teams skills list --source builtin" in result.output


def test_skills_show_help_describes_effective_skill_resolution() -> None:
    result = runner.invoke(cli_app.app, ["skills", "show", "--help"])

    assert result.exit_code == 0
    assert "Show the effective definition for a single skill." in result.output
    assert "skill shadows a built-in skill with the same name" in result.output
    assert "Skill name to inspect after scope merge and override" in result.output
    assert "agent-teams skills show time --format json" in result.output


def _build_registry(tmp_path: Path) -> SkillRegistry:
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    app_skills_dir = tmp_path / ".config" / "agent-teams" / "skills"

    _write_skill(
        builtin_skills_dir / "shared",
        name="shared",
        description="builtin shared skill",
        instructions="Builtin instructions.",
    )
    _write_skill(
        builtin_skills_dir / "builtin_only",
        name="builtin_only",
        description="builtin only skill",
        instructions="Builtin only instructions.",
    )
    _write_skill(
        app_skills_dir / "shared",
        name="shared",
        description="app shared skill",
        instructions="App instructions.",
    )
    _write_skill(
        app_skills_dir / "app_only",
        name="app_only",
        description="app only skill",
        instructions="App only instructions.",
    )

    return SkillRegistry(
        directory=SkillsDirectory(
            base_dir=app_skills_dir,
            fallback_dirs=(builtin_skills_dir,),
        )
    )


def _write_skill(
    skill_dir: Path, *, name: str, description: str, instructions: str
) -> None:
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{instructions}\n",
        encoding="utf-8",
    )
