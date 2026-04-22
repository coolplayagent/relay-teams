# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.skills import SkillRegistry, SkillsConfigReloadService


def test_reload_skills_config_ignores_unknown_skills_on_existing_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    builtin_skills_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "relay_teams.skills.discovery.get_builtin_skills_dir_path",
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


def test_reload_skills_config_omits_project_start_dir_when_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    role_registry = RoleRegistry()
    captured_kwargs: list[dict[str, object]] = []
    original_from_config_dirs = SkillRegistry.from_config_dirs

    def _fake_from_config_dirs(cls, **kwargs: object) -> SkillRegistry:
        captured_kwargs.append(dict(kwargs))
        return original_from_config_dirs(app_config_dir=app_config_dir)

    monkeypatch.setattr(
        "relay_teams.skills.config_reload_service.SkillRegistry.from_config_dirs",
        classmethod(_fake_from_config_dirs),
    )
    service = SkillsConfigReloadService(
        config_dir=app_config_dir,
        role_registry=role_registry,
        on_skill_reloaded=lambda _skill_registry: None,
    )

    service.reload_skills_config()

    assert captured_kwargs == [{"app_config_dir": app_config_dir}]


def test_reload_skills_config_uses_configured_project_start_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    project_dir = tmp_path / "project"
    role_registry = RoleRegistry()
    captured_kwargs: list[dict[str, object]] = []
    original_from_config_dirs = SkillRegistry.from_config_dirs

    project_dir.mkdir()

    def _fake_from_config_dirs(cls, **kwargs: object) -> SkillRegistry:
        captured_kwargs.append(dict(kwargs))
        return original_from_config_dirs(app_config_dir=app_config_dir)

    monkeypatch.setattr(
        "relay_teams.skills.config_reload_service.SkillRegistry.from_config_dirs",
        classmethod(_fake_from_config_dirs),
    )
    service = SkillsConfigReloadService(
        config_dir=app_config_dir,
        project_start_dir=project_dir,
        role_registry=role_registry,
        on_skill_reloaded=lambda _skill_registry: None,
    )

    service.reload_skills_config()

    assert captured_kwargs == [
        {
            "app_config_dir": app_config_dir,
            "project_start_dir": project_dir,
        }
    ]
