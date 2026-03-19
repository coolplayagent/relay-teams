# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.tools.runtime.approval_ticket_repo import (
        ApprovalTicketRecord,
        ApprovalTicketRepository,
        ApprovalTicketStatus,
        approval_signature_key,
    )
    from agent_teams.tools.runtime.approval_state import (
        ToolApprovalAction,
        ToolApprovalManager,
    )
    from agent_teams.tools.runtime.context import ToolContext, ToolDeps
    from agent_teams.tools.runtime.execution import execute_tool
    from agent_teams.tools.runtime.models import (
        ToolError,
        ToolInternalRecord,
        ToolResultEnvelope,
        ToolResultProjection,
    )
    from agent_teams.tools.runtime.policy import ToolApprovalPolicy

__all__ = [
    "ApprovalTicketRecord",
    "ApprovalTicketRepository",
    "ApprovalTicketStatus",
    "ToolApprovalAction",
    "ToolApprovalManager",
    "ToolApprovalPolicy",
    "ToolContext",
    "ToolDeps",
    "ToolError",
    "ToolInternalRecord",
    "ToolResultEnvelope",
    "ToolResultProjection",
    "approval_signature_key",
    "execute_tool",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ApprovalTicketRecord": (
        "agent_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketRecord",
    ),
    "ApprovalTicketRepository": (
        "agent_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketRepository",
    ),
    "ApprovalTicketStatus": (
        "agent_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketStatus",
    ),
    "ToolApprovalAction": (
        "agent_teams.tools.runtime.approval_state",
        "ToolApprovalAction",
    ),
    "ToolApprovalManager": (
        "agent_teams.tools.runtime.approval_state",
        "ToolApprovalManager",
    ),
    "ToolApprovalPolicy": (
        "agent_teams.tools.runtime.policy",
        "ToolApprovalPolicy",
    ),
    "ToolContext": ("agent_teams.tools.runtime.context", "ToolContext"),
    "ToolDeps": ("agent_teams.tools.runtime.context", "ToolDeps"),
    "ToolError": ("agent_teams.tools.runtime.models", "ToolError"),
    "ToolInternalRecord": (
        "agent_teams.tools.runtime.models",
        "ToolInternalRecord",
    ),
    "ToolResultEnvelope": (
        "agent_teams.tools.runtime.models",
        "ToolResultEnvelope",
    ),
    "ToolResultProjection": (
        "agent_teams.tools.runtime.models",
        "ToolResultProjection",
    ),
    "approval_signature_key": (
        "agent_teams.tools.runtime.approval_ticket_repo",
        "approval_signature_key",
    ),
    "execute_tool": ("agent_teams.tools.runtime.execution", "execute_tool"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
