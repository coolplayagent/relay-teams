# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import re

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app
from relay_teams.skills.discovery import SkillsDirectory
from relay_teams.skills.skill_models import SkillSource
from relay_teams.skills.skill_registry import SkillRegistry

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _normalized_output(text: str) -> str:
    return " ".join(_ANSI_ESCAPE_RE.sub("", text).split())


def test_skills_list_returns_effective_skill_entries_in_json_output(
    tmp_path: Path, monkeypatch
) -> None:
    registry = _build_registry(tmp_path)
    monkeypatch.setattr(
        "relay_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(cli_app.app, ["skills", "list", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == [
        {
            "ref": "app_only",
            "name": "app_only",
            "source": "user_relay_teams",
            "directory": (tmp_path / ".agent-teams" / "skills" / "app_only")
            .resolve()
            .as_posix(),
            "description": "app only skill",
        },
        {
            "ref": "builtin_only",
            "name": "builtin_only",
            "source": "builtin",
            "directory": (tmp_path / "builtin" / "skills" / "builtin_only")
            .resolve()
            .as_posix(),
            "description": "builtin only skill",
        },
        {
            "ref": "shared",
            "name": "shared",
            "source": "user_relay_teams",
            "directory": (tmp_path / ".agent-teams" / "skills" / "shared")
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
        "relay_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(
        cli_app.app, ["skills", "show", "shared", "--format", "json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ref"] == "shared"
    assert payload["name"] == "shared"
    assert payload["source"] == "user_relay_teams"
    assert payload["description"] == "app shared skill"
    assert (
        payload["manifest_path"]
        == (tmp_path / ".agent-teams" / "skills" / "shared" / "SKILL.md")
        .resolve()
        .as_posix()
    )
    assert payload["instructions"] == "App instructions."
    assert payload["files"] == [
        (tmp_path / ".agent-teams" / "skills" / "shared" / "SKILL.md")
        .resolve()
        .as_posix()
    ]


def test_skills_list_table_output_is_rendered(tmp_path: Path, monkeypatch) -> None:
    registry = _build_registry(tmp_path)
    monkeypatch.setattr(
        "relay_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(cli_app.app, ["skills", "list"])

    assert result.exit_code == 0
    assert result.output.startswith("Skills (3 total)")
    assert "| Name" in result.output
    assert "shared" in result.output
    assert "user_relay_teams" in result.output


def test_skills_help_explains_merge_order() -> None:
    result = runner.invoke(cli_app.app, ["skills", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert (
        "Inspect skills discovered from built-in, user, and project directories."
        in normalized_output
    )
    assert "~/.codex/skills" in normalized_output
    assert "~/.claude/skills" in normalized_output
    assert "~/.config/opencode/skills" in normalized_output
    assert "~/.relay-teams/skills" in normalized_output
    assert "~/.agents/skills" in normalized_output
    assert "the later source wins" in normalized_output
    assert "relay-teams skills show time" in normalized_output


def test_skills_list_help_includes_examples_and_source_behavior() -> None:
    result = runner.invoke(cli_app.app, ["skills", "list", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert (
        "List all discovered skills across builtin and app scopes." in normalized_output
    )
    assert "only the final winning entry is shown" in normalized_output
    assert "--source" in normalized_output
    assert "relay-teams skills list --source builtin" in normalized_output


def test_skills_show_help_describes_effective_skill_resolution() -> None:
    result = runner.invoke(cli_app.app, ["skills", "show", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert "Show a single skill definition." in normalized_output
    assert "The argument is the skill name." in normalized_output
    assert "Skill name to inspect." in normalized_output
    assert "relay-teams skills show time --format json" in normalized_output


def test_skills_list_can_filter_project_agents_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_agents_dir = tmp_path / "repo" / ".agents" / "skills"
    _write_skill(
        project_agents_dir / "time",
        name="time",
        description="project agents time skill",
        instructions="Use project time.",
    )
    registry = SkillRegistry(
        directory=SkillsDirectory(
            sources=((SkillSource.PROJECT_AGENTS, project_agents_dir),)
        )
    )
    monkeypatch.setattr(
        "relay_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(
        cli_app.app,
        ["skills", "list", "--source", "project_agents", "--format", "json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        {
            "ref": "time",
            "name": "time",
            "source": "project_agents",
            "directory": (project_agents_dir / "time").resolve().as_posix(),
            "description": "project agents time skill",
        }
    ]


def test_skills_list_can_filter_project_opencode_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_opencode_dir = tmp_path / "repo" / ".opencode" / "skills"
    _write_skill(
        project_opencode_dir / "openspec-propose",
        name="openspec-propose",
        description="OpenCode OpenSpec proposal skill",
        instructions="Create an OpenSpec proposal.",
    )
    registry = SkillRegistry(
        directory=SkillsDirectory(
            sources=((SkillSource.PROJECT_OPENCODE, project_opencode_dir),)
        )
    )
    monkeypatch.setattr(
        "relay_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(
        cli_app.app,
        ["skills", "list", "--source", "project_opencode", "--format", "json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        {
            "ref": "openspec-propose",
            "name": "openspec-propose",
            "source": "project_opencode",
            "directory": (project_opencode_dir / "openspec-propose")
            .resolve()
            .as_posix(),
            "description": "OpenCode OpenSpec proposal skill",
        }
    ]


def test_skills_list_can_filter_user_opencode_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_opencode_dir = tmp_path / "home" / ".config" / "opencode" / "skills"
    _write_skill(
        user_opencode_dir / "global-plan",
        name="global-plan",
        description="OpenCode global plan skill",
        instructions="Create a global plan.",
    )
    registry = SkillRegistry(
        directory=SkillsDirectory(
            sources=((SkillSource.USER_OPENCODE, user_opencode_dir),)
        )
    )
    monkeypatch.setattr(
        "relay_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(
        cli_app.app,
        ["skills", "list", "--source", "user_opencode", "--format", "json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        {
            "ref": "global-plan",
            "name": "global-plan",
            "source": "user_opencode",
            "directory": (user_opencode_dir / "global-plan").resolve().as_posix(),
            "description": "OpenCode global plan skill",
        }
    ]


def _build_registry(tmp_path: Path) -> SkillRegistry:
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    app_skills_dir = tmp_path / ".agent-teams" / "skills"

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
            sources=(
                (SkillSource.BUILTIN, builtin_skills_dir),
                (SkillSource.USER_RELAY_TEAMS, app_skills_dir),
            )
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
