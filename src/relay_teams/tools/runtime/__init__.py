# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.tools.runtime.approval_ticket_repo import (
        ApprovalTicketRecord,
        ApprovalTicketRepository,
        ApprovalTicketStatus,
        approval_signature_key,
    )
    from relay_teams.tools.runtime.approval_state import (
        ToolApprovalAction,
        ToolApprovalManager,
    )
    from relay_teams.tools.runtime.context import ToolContext, ToolDeps
    from relay_teams.tools.runtime.execution import execute_tool
    from relay_teams.tools.runtime.models import (
        ToolApprovalDecision,
        ToolApprovalRequest,
        ToolError,
        ToolExecutionError,
        ToolInternalRecord,
        ToolResultEnvelope,
        ToolResultProjection,
    )
    from relay_teams.tools.runtime.policy import ToolApprovalPolicy

__all__ = [
    "ApprovalTicketRecord",
    "ApprovalTicketRepository",
    "ApprovalTicketStatus",
    "ToolApprovalAction",
    "ToolApprovalDecision",
    "ToolApprovalManager",
    "ToolApprovalPolicy",
    "ToolApprovalRequest",
    "ToolContext",
    "ToolDeps",
    "ToolError",
    "ToolExecutionError",
    "ToolInternalRecord",
    "ToolResultEnvelope",
    "ToolResultProjection",
    "approval_signature_key",
    "execute_tool",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ApprovalTicketRecord": (
        "relay_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketRecord",
    ),
    "ApprovalTicketRepository": (
        "relay_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketRepository",
    ),
    "ApprovalTicketStatus": (
        "relay_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketStatus",
    ),
    "ToolApprovalAction": (
        "relay_teams.tools.runtime.approval_state",
        "ToolApprovalAction",
    ),
    "ToolApprovalDecision": (
        "relay_teams.tools.runtime.models",
        "ToolApprovalDecision",
    ),
    "ToolApprovalManager": (
        "relay_teams.tools.runtime.approval_state",
        "ToolApprovalManager",
    ),
    "ToolApprovalPolicy": (
        "relay_teams.tools.runtime.policy",
        "ToolApprovalPolicy",
    ),
    "ToolApprovalRequest": (
        "relay_teams.tools.runtime.models",
        "ToolApprovalRequest",
    ),
    "ToolContext": ("relay_teams.tools.runtime.context", "ToolContext"),
    "ToolDeps": ("relay_teams.tools.runtime.context", "ToolDeps"),
    "ToolError": ("relay_teams.tools.runtime.models", "ToolError"),
    "ToolExecutionError": (
        "relay_teams.tools.runtime.models",
        "ToolExecutionError",
    ),
    "ToolInternalRecord": (
        "relay_teams.tools.runtime.models",
        "ToolInternalRecord",
    ),
    "ToolResultEnvelope": (
        "relay_teams.tools.runtime.models",
        "ToolResultEnvelope",
    ),
    "ToolResultProjection": (
        "relay_teams.tools.runtime.models",
        "ToolResultProjection",
    ),
    "approval_signature_key": (
        "relay_teams.tools.runtime.approval_ticket_repo",
        "approval_signature_key",
    ),
    "execute_tool": ("relay_teams.tools.runtime.execution", "execute_tool"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
