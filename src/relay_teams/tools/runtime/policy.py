# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.computer import ComputerActionRisk
from pydantic import BaseModel, ConfigDict

from relay_teams.tools.runtime.models import (
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolRuntimeDecision,
)


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


class ToolRuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    yolo: bool = False
    approval_required_tools: frozenset[str] = DEFAULT_APPROVAL_REQUIRED_TOOLS
    denied_tools: frozenset[str] = frozenset()
    timeout_seconds: float = 300.0

    def requires_approval(self, tool_name: str) -> bool:
        return self.evaluate(tool_name).required

    def evaluate(
        self,
        tool_name: str,
        request: ToolApprovalRequest | None = None,
        *,
        role_id: str = "",
        task_id: str = "",
        allowed_tools: tuple[str, ...] | None = None,
    ) -> ToolApprovalDecision:
        _ = task_id
        if tool_name in self.denied_tools:
            return _runtime_decision(
                required=False,
                runtime_decision=ToolRuntimeDecision.DENY,
                reason=f"Tool {tool_name} is denied by runtime policy.",
                request=request,
            )
        if allowed_tools is not None and tool_name not in allowed_tools:
            role_label = role_id or "current role"
            return _runtime_decision(
                required=False,
                runtime_decision=ToolRuntimeDecision.DENY,
                reason=f"Tool {tool_name} is not authorized for {role_label}.",
                request=request,
            )
        if self.yolo:
            return _runtime_decision(
                required=False,
                runtime_decision=ToolRuntimeDecision.ALLOW,
                request=request,
            )
        if request is not None:
            if request.risk_level in {
                ComputerActionRisk.GUARDED,
                ComputerActionRisk.DESTRUCTIVE,
            }:
                return _runtime_decision(
                    required=True,
                    runtime_decision=ToolRuntimeDecision.REQUIRE_APPROVAL,
                    request=request,
                )
            if request.risk_level == ComputerActionRisk.SAFE:
                return _runtime_decision(
                    required=False,
                    runtime_decision=ToolRuntimeDecision.ALLOW,
                    request=request,
                )
        required = tool_name in self.approval_required_tools
        return _runtime_decision(
            required=required,
            runtime_decision=(
                ToolRuntimeDecision.REQUIRE_APPROVAL
                if required
                else ToolRuntimeDecision.ALLOW
            ),
            request=request,
        )


class ToolApprovalPolicy(ToolRuntimePolicy):
    def with_yolo(self, yolo: bool) -> ToolApprovalPolicy:
        if yolo == self.yolo:
            return self
        return ToolApprovalPolicy(
            yolo=yolo,
            approval_required_tools=self.approval_required_tools,
            denied_tools=self.denied_tools,
            timeout_seconds=self.timeout_seconds,
        )


def _runtime_decision(
    *,
    required: bool,
    runtime_decision: ToolRuntimeDecision,
    request: ToolApprovalRequest | None = None,
    reason: str = "",
) -> ToolApprovalDecision:
    return ToolApprovalDecision(
        required=required,
        runtime_decision=runtime_decision,
        reason=reason,
        permission_scope=request.permission_scope if request is not None else None,
        risk_level=request.risk_level if request is not None else None,
        target_summary=request.target_summary if request is not None else "",
        source=request.source if request is not None else "",
        execution_surface=request.execution_surface if request is not None else None,
    )
