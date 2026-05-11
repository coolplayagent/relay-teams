# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.computer import ComputerActionRisk
from pydantic import BaseModel, ConfigDict, Field

from relay_teams.tools.runtime.guardrails import RuntimeGuardrailPolicy
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
    shell_safety_policy_enabled: bool = True
    approval_required_tools: frozenset[str] = DEFAULT_APPROVAL_REQUIRED_TOOLS
    denied_tools: frozenset[str] = frozenset()
    guardrails: RuntimeGuardrailPolicy = Field(default_factory=RuntimeGuardrailPolicy)
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
        return self.with_runtime_overrides(yolo=yolo)

    def with_runtime_overrides(
        self,
        *,
        yolo: bool | None = None,
        shell_safety_policy_enabled: bool | None = None,
    ) -> ToolApprovalPolicy:
        next_yolo = self.yolo if yolo is None else yolo
        next_shell_safety_policy_enabled = (
            self.shell_safety_policy_enabled
            if shell_safety_policy_enabled is None
            else shell_safety_policy_enabled
        )
        if (
            next_yolo == self.yolo
            and next_shell_safety_policy_enabled == self.shell_safety_policy_enabled
        ):
            return self
        return ToolApprovalPolicy(
            yolo=next_yolo,
            shell_safety_policy_enabled=next_shell_safety_policy_enabled,
            approval_required_tools=self.approval_required_tools,
            denied_tools=self.denied_tools,
            guardrails=self.guardrails,
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
