# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.interfaces.server.runtime_identity import build_skill_registry_sanity
from relay_teams.skills.discovery import SkillsDirectory
from relay_teams.skills.skill_models import SkillSource
from relay_teams.skills.skill_registry import SkillRegistry


def test_build_skill_registry_sanity_uses_injected_registry_projection(
    tmp_path: Path,
) -> None:
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    user_skills_dir = tmp_path / "user" / "skills"
    _write_skill(
        builtin_skills_dir / "deepresearch",
        name="deepresearch",
        description="builtin deepresearch",
    )
    _write_skill(
        builtin_skills_dir / "diff",
        name="diff",
        description="builtin diff",
    )
    _write_skill(
        user_skills_dir / "deepresearch",
        name="deepresearch",
        description="user override",
    )
    registry = SkillRegistry(
        directory=SkillsDirectory(
            sources=(
                (SkillSource.BUILTIN, builtin_skills_dir),
                (SkillSource.USER_RELAY_TEAMS, user_skills_dir),
            )
        )
    )

    sanity = build_skill_registry_sanity(skill_registry=registry)

    assert sanity.builtin_skill_count == 1
    assert sanity.builtin_skill_names == ("diff",)
    assert sanity.has_builtin_deepresearch is False


def _write_skill(skill_dir: Path, *, name: str, description: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (f"---\nname: {name}\ndescription: {description}\n---\nUse this skill.\n"),
        encoding="utf-8",
    )
