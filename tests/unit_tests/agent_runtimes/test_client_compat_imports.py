# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from types import ModuleType

_COMPAT_MODULES = (
    "relay_teams.agent_runtimes.a2a_client",
    "relay_teams.agent_runtimes.acp_client",
    "relay_teams.agent_runtimes.cli_client",
)


def test_agent_runtime_client_compat_submodules_import() -> None:
    imported = tuple(importlib.import_module(name) for name in _COMPAT_MODULES)

    assert tuple(module.__name__ for module in imported) == _COMPAT_MODULES
    assert all(isinstance(module, ModuleType) for module in imported)
