# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


DEFAULT_APPROVAL_REQUIRED_TOOLS = frozenset(
    {
        "create_tasks",
        "dispatch_task",
        "update_task",
        "shell",
        "write",
        "write_stage_doc",
    }
)


class ToolApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_required_tools: frozenset[str] = DEFAULT_APPROVAL_REQUIRED_TOOLS
    timeout_seconds: float = 300.0

    def requires_approval(self, tool_name: str) -> bool:
        return tool_name in self.approval_required_tools
