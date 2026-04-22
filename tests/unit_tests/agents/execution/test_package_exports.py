# -*- coding: utf-8 -*-
from __future__ import annotations

from importlib import import_module


def test_importing_skills_registry_does_not_trigger_coordination_cycle() -> None:
    module = import_module("relay_teams.skills.skill_registry")

    skill_registry = getattr(module, "SkillRegistry", None)
    assert skill_registry is not None


def test_execution_package_requires_direct_coordination_agent_import() -> None:
    package = import_module("relay_teams.agents.execution")
    module = import_module("relay_teams.agents.execution.coordination_agent_builder")

    assert getattr(package, "build_coordination_agent", None) is None
    assert callable(getattr(module, "build_coordination_agent", None))
