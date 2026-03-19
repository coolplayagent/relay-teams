# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.tools.registry import (
        ToolRegister,
        ToolRegistry,
        build_default_registry,
    )
    from agent_teams.tools.runtime import (
        ToolApprovalAction,
        ToolApprovalManager,
        ToolApprovalPolicy,
        ToolContext,
        ToolDeps,
        ToolError,
        ToolInternalRecord,
        ToolResultEnvelope,
        ToolResultProjection,
        execute_tool,
    )

__all__ = [
    "ToolApprovalAction",
    "ToolApprovalManager",
    "ToolApprovalPolicy",
    "ToolContext",
    "ToolDeps",
    "ToolError",
    "ToolInternalRecord",
    "ToolRegister",
    "ToolRegistry",
    "ToolResultEnvelope",
    "ToolResultProjection",
    "build_default_registry",
    "execute_tool",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ToolApprovalAction": (
        "agent_teams.tools.runtime",
        "ToolApprovalAction",
    ),
    "ToolApprovalManager": (
        "agent_teams.tools.runtime",
        "ToolApprovalManager",
    ),
    "ToolApprovalPolicy": (
        "agent_teams.tools.runtime",
        "ToolApprovalPolicy",
    ),
    "ToolContext": ("agent_teams.tools.runtime", "ToolContext"),
    "ToolDeps": ("agent_teams.tools.runtime", "ToolDeps"),
    "ToolError": ("agent_teams.tools.runtime", "ToolError"),
    "ToolInternalRecord": ("agent_teams.tools.runtime", "ToolInternalRecord"),
    "ToolRegister": ("agent_teams.tools.registry", "ToolRegister"),
    "ToolRegistry": ("agent_teams.tools.registry", "ToolRegistry"),
    "ToolResultEnvelope": (
        "agent_teams.tools.runtime",
        "ToolResultEnvelope",
    ),
    "ToolResultProjection": (
        "agent_teams.tools.runtime",
        "ToolResultProjection",
    ),
    "build_default_registry": (
        "agent_teams.tools.registry",
        "build_default_registry",
    ),
    "execute_tool": ("agent_teams.tools.runtime", "execute_tool"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
