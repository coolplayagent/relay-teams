# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import re

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app
from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.skills.skill_registry import SkillRegistry

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _normalized_output(text: str) -> str:
    return " ".join(_ANSI_ESCAPE_RE.sub("", text).split())


def test_skills_list_returns_builtin_and_app_skill_entries_in_json_output(
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
            "ref": "app:app_only",
            "name": "app_only",
            "source": "app",
            "directory": (tmp_path / ".agent-teams" / "skills" / "app_only")
            .resolve()
            .as_posix(),
            "description": "app only skill",
        },
        {
            "ref": "builtin:builtin_only",
            "name": "builtin_only",
            "source": "builtin",
            "directory": (tmp_path / "builtin" / "skills" / "builtin_only")
            .resolve()
            .as_posix(),
            "description": "builtin only skill",
        },
        {
            "ref": "app:shared",
            "name": "shared",
            "source": "app",
            "directory": (tmp_path / ".agent-teams" / "skills" / "shared")
            .resolve()
            .as_posix(),
            "description": "app shared skill",
        },
        {
            "ref": "builtin:shared",
            "name": "shared",
            "source": "builtin",
            "directory": (tmp_path / "builtin" / "skills" / "shared")
            .resolve()
            .as_posix(),
            "description": "builtin shared skill",
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
        cli_app.app, ["skills", "show", "app:shared", "--format", "json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ref"] == "app:shared"
    assert payload["name"] == "shared"
    assert payload["source"] == "app"
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
        "agent_teams.skills.skill_cli.load_skill_registry", lambda: registry
    )

    result = runner.invoke(cli_app.app, ["skills", "list"])

    assert result.exit_code == 0
    assert result.output.startswith("Skills (4 total)")
    assert "| Name" in result.output
    assert "shared" in result.output
    assert "app" in result.output


def test_skills_help_explains_merge_order() -> None:
    result = runner.invoke(cli_app.app, ["skills", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert (
        "Inspect skills discovered from built-in defaults and the app directory."
        in normalized_output
    )
    assert "~/.agent-teams/skills" in normalized_output
    assert "both entries are kept" in normalized_output
    assert "relay-teams skills show time" in normalized_output


def test_skills_list_help_includes_examples_and_source_behavior() -> None:
    result = runner.invoke(cli_app.app, ["skills", "list", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert (
        "List all discovered skills across builtin and app scopes." in normalized_output
    )
    assert "both entries are shown" in normalized_output
    assert "--source" in normalized_output
    assert "relay-teams skills list --source builtin" in normalized_output


def test_skills_show_help_describes_effective_skill_resolution() -> None:
    result = runner.invoke(cli_app.app, ["skills", "show", "--help"])
    normalized_output = _normalized_output(result.output)

    assert result.exit_code == 0
    assert "Show a single skill definition." in normalized_output
    assert "canonical ref such as app:time or builtin:time" in normalized_output
    assert "Skill canonical ref or unique plain name to inspect." in normalized_output
    assert "relay-teams skills show time --format json" in normalized_output


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
