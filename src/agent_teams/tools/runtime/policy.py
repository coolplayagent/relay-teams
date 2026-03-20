# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


DEFAULT_APPROVAL_REQUIRED_TOOLS = frozenset(
    {
        "create_tasks",
        "dispatch_task",
        "update_task",
        "shell",
        "edit",
        "write",
        "write_tmp",
    }
)


class ToolApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    yolo: bool = False
    approval_required_tools: frozenset[str] = DEFAULT_APPROVAL_REQUIRED_TOOLS
    timeout_seconds: float = 300.0

    def requires_approval(self, tool_name: str) -> bool:
        if self.yolo:
            return False
        return tool_name in self.approval_required_tools

    def with_yolo(self, yolo: bool) -> ToolApprovalPolicy:
        if yolo == self.yolo:
            return self
        return ToolApprovalPolicy(
            yolo=yolo,
            approval_required_tools=self.approval_required_tools,
            timeout_seconds=self.timeout_seconds,
        )
