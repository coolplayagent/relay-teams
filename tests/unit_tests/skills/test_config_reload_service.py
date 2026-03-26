# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.skills import SkillRegistry, SkillsConfigReloadService


def test_reload_skills_config_ignores_unknown_skills_on_existing_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    builtin_skills_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "agent_teams.skills.discovery.get_builtin_skills_dir_path",
        lambda: builtin_skills_dir.resolve(),
    )
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="writer",
            name="Writer",
            description="Writes documents.",
            version="1.0.0",
            tools=(),
            skills=("missing_skill",),
            model_profile="default",
            system_prompt="Write clearly.",
        )
    )
    reloaded_registries: list[SkillRegistry] = []
    service = SkillsConfigReloadService(
        config_dir=app_config_dir,
        role_registry=role_registry,
        on_skill_reloaded=lambda skill_registry: reloaded_registries.append(
            skill_registry
        ),
    )

    service.reload_skills_config()

    assert len(reloaded_registries) == 1
    assert reloaded_registries[0].list_names() == ()
