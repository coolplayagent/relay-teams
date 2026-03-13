# -*- coding: utf-8 -*-
from __future__ import annotations

from importlib import import_module


def test_importing_skills_registry_does_not_trigger_coordination_cycle() -> None:
    module = import_module("agent_teams.skills.registry")

    skill_registry = getattr(module, "SkillRegistry", None)
    assert skill_registry is not None


def test_orchestration_package_exports_build_coordination_agent_lazily() -> None:
    module = import_module("agent_teams.agents.execution")

    exported = getattr(module, "build_coordination_agent", None)
    assert callable(exported)
