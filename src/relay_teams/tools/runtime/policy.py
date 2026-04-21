# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.computer import ComputerActionRisk
from pydantic import BaseModel, ConfigDict

from relay_teams.tools.runtime.models import ToolApprovalDecision, ToolApprovalRequest


DEFAULT_APPROVAL_REQUIRED_TOOLS = frozenset(
    {
        "orch_create_tasks",
        "orch_dispatch_task",
        "orch_update_task",
        "shell",
        "edit",
        "write",
        "write_tmp",
        "webfetch",
        "websearch",
    }
)


class ToolApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    yolo: bool = False
    approval_required_tools: frozenset[str] = DEFAULT_APPROVAL_REQUIRED_TOOLS
    timeout_seconds: float = 300.0

    def requires_approval(self, tool_name: str) -> bool:
        return self.evaluate(tool_name).required

    def evaluate(
        self,
        tool_name: str,
        request: ToolApprovalRequest | None = None,
    ) -> ToolApprovalDecision:
        if self.yolo:
            return ToolApprovalDecision(required=False)
        if request is not None:
            if request.risk_level in {
                ComputerActionRisk.GUARDED,
                ComputerActionRisk.DESTRUCTIVE,
            }:
                return ToolApprovalDecision(
                    required=True,
                    permission_scope=request.permission_scope,
                    risk_level=request.risk_level,
                    target_summary=request.target_summary,
                    source=request.source,
                    execution_surface=request.execution_surface,
                )
            if request.risk_level == ComputerActionRisk.SAFE:
                return ToolApprovalDecision(
                    required=False,
                    permission_scope=request.permission_scope,
                    risk_level=request.risk_level,
                    target_summary=request.target_summary,
                    source=request.source,
                    execution_surface=request.execution_surface,
                )
        return ToolApprovalDecision(required=tool_name in self.approval_required_tools)

    def with_yolo(self, yolo: bool) -> ToolApprovalPolicy:
        if yolo == self.yolo:
            return self
        return ToolApprovalPolicy(
            yolo=yolo,
            approval_required_tools=self.approval_required_tools,
            timeout_seconds=self.timeout_seconds,
        )
