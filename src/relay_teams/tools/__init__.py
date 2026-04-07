# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.tools.registry import (
        ToolAvailabilityRecord,
        ToolImplicitResolver,
        ToolRegister,
        ToolRegistry,
        ToolResolutionContext,
        build_default_registry,
    )
    from relay_teams.tools.runtime import (
        ToolApprovalDecision,
        ToolApprovalAction,
        ToolApprovalManager,
        ToolApprovalPolicy,
        ToolApprovalRequest,
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
    "ToolApprovalDecision",
    "ToolApprovalManager",
    "ToolApprovalPolicy",
    "ToolApprovalRequest",
    "ToolAvailabilityRecord",
    "ToolContext",
    "ToolDeps",
    "ToolError",
    "ToolInternalRecord",
    "ToolImplicitResolver",
    "ToolRegister",
    "ToolRegistry",
    "ToolResultEnvelope",
    "ToolResultProjection",
    "ToolResolutionContext",
    "build_default_registry",
    "execute_tool",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ToolApprovalAction": (
        "relay_teams.tools.runtime",
        "ToolApprovalAction",
    ),
    "ToolApprovalDecision": (
        "relay_teams.tools.runtime",
        "ToolApprovalDecision",
    ),
    "ToolApprovalManager": (
        "relay_teams.tools.runtime",
        "ToolApprovalManager",
    ),
    "ToolApprovalPolicy": (
        "relay_teams.tools.runtime",
        "ToolApprovalPolicy",
    ),
    "ToolApprovalRequest": (
        "relay_teams.tools.runtime",
        "ToolApprovalRequest",
    ),
    "ToolAvailabilityRecord": (
        "relay_teams.tools.registry",
        "ToolAvailabilityRecord",
    ),
    "ToolContext": ("relay_teams.tools.runtime", "ToolContext"),
    "ToolDeps": ("relay_teams.tools.runtime", "ToolDeps"),
    "ToolError": ("relay_teams.tools.runtime", "ToolError"),
    "ToolInternalRecord": ("relay_teams.tools.runtime", "ToolInternalRecord"),
    "ToolImplicitResolver": (
        "relay_teams.tools.registry",
        "ToolImplicitResolver",
    ),
    "ToolRegister": ("relay_teams.tools.registry", "ToolRegister"),
    "ToolRegistry": ("relay_teams.tools.registry", "ToolRegistry"),
    "ToolResultEnvelope": (
        "relay_teams.tools.runtime",
        "ToolResultEnvelope",
    ),
    "ToolResultProjection": (
        "relay_teams.tools.runtime",
        "ToolResultProjection",
    ),
    "ToolResolutionContext": (
        "relay_teams.tools.registry",
        "ToolResolutionContext",
    ),
    "build_default_registry": (
        "relay_teams.tools.registry",
        "build_default_registry",
    ),
    "execute_tool": ("relay_teams.tools.runtime", "execute_tool"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
