# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from types import ModuleType

from relay_teams.agents.instances.models import AgentRuntimeRecord

_COMPAT_MODULES = (
    "relay_teams.agents.instances",
    "relay_teams.agents.instances.enums",
    "relay_teams.agents.instances.ids",
    "relay_teams.agents.instances.instance_repository",
    "relay_teams.agents.instances.models",
)


def test_agents_instances_reexports_runtime_instance_model() -> None:
    assert (
        AgentRuntimeRecord.__module__ == "relay_teams.agent_runtimes.instances.models"
    )


def test_agents_instances_compat_submodules_import() -> None:
    imported = tuple(importlib.import_module(name) for name in _COMPAT_MODULES)

    assert tuple(module.__name__ for module in imported) == _COMPAT_MODULES
    assert all(isinstance(module, ModuleType) for module in imported)
