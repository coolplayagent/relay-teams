# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent_teams.sessions.runs.enums import ApprovalMode


DEFAULT_APPROVAL_REQUIRED_TOOLS = frozenset(
    {
        "create_tasks",
        "dispatch_task",
        "update_task",
        "shell",
        "edit",
        "write",
    }
)


class ToolApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_mode: ApprovalMode = ApprovalMode.STANDARD
    approval_required_tools: frozenset[str] = DEFAULT_APPROVAL_REQUIRED_TOOLS
    timeout_seconds: float = 300.0

    def requires_approval(self, tool_name: str) -> bool:
        if self.approval_mode == ApprovalMode.YOLO:
            return False
        return tool_name in self.approval_required_tools

    def with_mode(self, approval_mode: ApprovalMode) -> ToolApprovalPolicy:
        if approval_mode == self.approval_mode:
            return self
        return ToolApprovalPolicy(
            approval_mode=approval_mode,
            approval_required_tools=self.approval_required_tools,
            timeout_seconds=self.timeout_seconds,
        )
