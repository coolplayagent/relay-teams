# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from types import ModuleType

_COMPAT_MODULES = (
    "relay_teams.agents.orchestration.a2a_bus",
    "relay_teams.agents.orchestration.a2a_bus_models",
    "relay_teams.agents.orchestration.a2a_tool",
)


def test_a2a_compat_submodules_import() -> None:
    imported = tuple(importlib.import_module(name) for name in _COMPAT_MODULES)

    assert tuple(module.__name__ for module in imported) == _COMPAT_MODULES
    assert all(isinstance(module, ModuleType) for module in imported)
