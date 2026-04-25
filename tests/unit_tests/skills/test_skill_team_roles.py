# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.roles.role_models import RoleMode
from relay_teams.skills.discovery import SkillsDirectory
from relay_teams.skills.skill_models import Skill
from relay_teams.skills.skill_models import SkillSource
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.skills.skill_team_roles import (
    build_skill_team_effective_role_id,
    build_skill_team_role_spec,
    list_skill_team_roles,
    summarize_skill_team_role,
)


def test_list_skill_team_roles_summarizes_roles_without_system_prompt(
    tmp_path: Path,
) -> None:
    skill = _write_team_skill(tmp_path)

    roles = list_skill_team_roles(skill)

    assert len(roles) == 1
    summary = roles[0].summary
    assert summary.role_id == "analyst"
    assert summary.effective_role_id.startswith("skill_team_team_review_analyst_")
    assert summary.name == "Research Analyst"
    assert summary.description == "Collects evidence for review."
    assert summary.tools == ("read", "office_read_markdown")
    assert summary.source_path == "contributors/analyst.md"
    assert "SYSTEM PROMPT" not in summary.model_dump_json()


def test_build_skill_team_role_spec_forces_subagent_mode(tmp_path: Path) -> None:
    skill = _write_team_skill(tmp_path)
    role_entry = list_skill_team_roles(skill)[0]

    spec = build_skill_team_role_spec(skill=skill, role=role_entry.role)

    assert spec.role_id == role_entry.summary.effective_role_id
    assert spec.mode == RoleMode.SUBAGENT
    assert spec.system_prompt == "SYSTEM PROMPT FOR ANALYST."
    assert spec.tools == ("read", "office_read_markdown")


def test_list_skill_team_roles_reports_activation_effective_tools(
    tmp_path: Path,
) -> None:
    skill = _write_team_skill(tmp_path, role_mode=RoleMode.PRIMARY)

    role_entry = list_skill_team_roles(skill)[0]

    assert "todo_write" in role_entry.role.tools
    assert role_entry.summary.tools == ("read", "office_read_markdown")


def test_list_skill_team_roles_ignores_invalid_and_duplicate_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _write_team_skill(tmp_path)
    invalid_dir = skill.directory / "notes"
    duplicate_dir = skill.directory / "specialists"
    invalid_dir.mkdir()
    duplicate_dir.mkdir()
    (invalid_dir / "invalid.md").write_text(
        "---\n"
        "role_id: invalid\n"
        "name: Invalid\n"
        "description: Missing tools.\n"
        "version: 1\n"
        "---\n"
        "Invalid.\n",
        encoding="utf-8",
    )
    (invalid_dir / "malformed.md").write_text(
        "---\nrole_id: [malformed\n---\nMalformed YAML must not abort discovery.\n",
        encoding="utf-8",
    )
    (invalid_dir / "invalid-summary.md").write_text(
        "---\n"
        "role_id: invalid_summary\n"
        "name: Invalid Summary\n"
        "description: Has role fields but invalid summary fields.\n"
        "version: 1\n"
        "model_profile: ''\n"
        "tools:\n"
        "  - read\n"
        "---\n"
        "Invalid summary must not abort discovery.\n",
        encoding="utf-8",
    )
    unreadable_path = invalid_dir / "unreadable.md"
    unreadable_path.write_text(
        "---\n"
        "role_id: unreadable\n"
        "name: Unreadable\n"
        "description: Simulates an I/O failure.\n"
        "version: 1\n"
        "tools:\n"
        "  - read\n"
        "---\n"
        "Unreadable.\n",
        encoding="utf-8",
    )
    (duplicate_dir / "duplicate.md").write_text(
        "---\n"
        "role_id: analyst\n"
        "name: Duplicate Analyst\n"
        "description: Duplicate role id.\n"
        "version: 1\n"
        "tools:\n"
        "  - read\n"
        "---\n"
        "Duplicate.\n",
        encoding="utf-8",
    )
    original_read_text = Path.read_text

    def fake_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if self == unreadable_path:
            raise OSError("permission denied")
        return original_read_text(
            self,
            encoding=encoding,
            errors=errors,
        )

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    roles = list_skill_team_roles(skill)

    assert [entry.summary.name for entry in roles] == ["Research Analyst"]


def test_skill_team_role_summary_handles_external_source_paths(tmp_path: Path) -> None:
    skill = _write_team_skill(tmp_path)
    role = list_skill_team_roles(skill)[0].role.model_copy(
        update={"source_path": tmp_path / "external.md"}
    )

    summary = summarize_skill_team_role(skill=skill, role=role)

    assert summary.source_path == (tmp_path / "external.md").resolve().as_posix()


def test_skill_team_effective_role_id_uses_fallback_fragments() -> None:
    role_id = build_skill_team_effective_role_id(skill_name="技能", role_id="角色")

    assert role_id.startswith("skill_team_skill_role_")


def _write_team_skill(
    tmp_path: Path,
    *,
    role_mode: RoleMode = RoleMode.SUBAGENT,
) -> Skill:
    skill_dir = tmp_path / "skills" / "team-review"
    contributors_dir = skill_dir / "contributors"
    contributors_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: team-review\n"
        "description: Coordinate a review team.\n"
        "---\n"
        "Use the review workflow.\n",
        encoding="utf-8",
    )
    (contributors_dir / "analyst.md").write_text(
        "---\n"
        "role_id: analyst\n"
        "name: Research Analyst\n"
        "description: Collects evidence for review.\n"
        "version: 1\n"
        f"mode: {role_mode.value}\n"
        "tools:\n"
        "  - read\n"
        "---\n"
        "SYSTEM PROMPT FOR ANALYST.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(
        directory=SkillsDirectory(
            sources=((SkillSource.USER_RELAY_TEAMS, tmp_path / "skills"),)
        )
    )
    skill = registry.get_skill_definition("team-review")
    assert skill is not None
    return skill
