# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.tools.registry.defaults import build_default_registry
    from relay_teams.tools.registry.registry import (
        ToolAvailabilityRecord,
        ToolImplicitResolver,
        ToolRegister,
        ToolRegistry,
        ToolResolutionContext,
    )
    from relay_teams.tools.registry.tool_groups import (
        ToolGroupDefinition,
        list_default_tool_groups,
    )

__all__ = [
    "ToolAvailabilityRecord",
    "ToolImplicitResolver",
    "ToolRegister",
    "ToolRegistry",
    "ToolResolutionContext",
    "ToolGroupDefinition",
    "build_default_registry",
    "list_default_tool_groups",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ToolAvailabilityRecord": (
        "relay_teams.tools.registry.registry",
        "ToolAvailabilityRecord",
    ),
    "ToolImplicitResolver": (
        "relay_teams.tools.registry.registry",
        "ToolImplicitResolver",
    ),
    "ToolRegister": ("relay_teams.tools.registry.registry", "ToolRegister"),
    "ToolRegistry": ("relay_teams.tools.registry.registry", "ToolRegistry"),
    "ToolResolutionContext": (
        "relay_teams.tools.registry.registry",
        "ToolResolutionContext",
    ),
    "ToolGroupDefinition": (
        "relay_teams.tools.registry.tool_groups",
        "ToolGroupDefinition",
    ),
    "build_default_registry": (
        "relay_teams.tools.registry.defaults",
        "build_default_registry",
    ),
    "list_default_tool_groups": (
        "relay_teams.tools.registry.tool_groups",
        "list_default_tool_groups",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
