# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.tools.registry.defaults import build_default_registry
    from agent_teams.tools.registry.registry import (
        ToolImplicitResolver,
        ToolRegister,
        ToolRegistry,
        ToolResolutionContext,
    )

__all__ = [
    "ToolImplicitResolver",
    "ToolRegister",
    "ToolRegistry",
    "ToolResolutionContext",
    "build_default_registry",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ToolImplicitResolver": (
        "agent_teams.tools.registry.registry",
        "ToolImplicitResolver",
    ),
    "ToolRegister": ("agent_teams.tools.registry.registry", "ToolRegister"),
    "ToolRegistry": ("agent_teams.tools.registry.registry", "ToolRegistry"),
    "ToolResolutionContext": (
        "agent_teams.tools.registry.registry",
        "ToolResolutionContext",
    ),
    "build_default_registry": (
        "agent_teams.tools.registry.defaults",
        "build_default_registry",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
