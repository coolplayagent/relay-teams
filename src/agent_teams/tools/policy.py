from __future__ import annotations

from dataclasses import dataclass


DEFAULT_APPROVAL_REQUIRED_TOOLS = frozenset(
    {
        'create_workflow_graph',
        'dispatch_ready_tasks',
        'shell',
        'write',
        'write_stage_doc',
    }
)


@dataclass(frozen=True)
class ToolApprovalPolicy:
    approval_required_tools: frozenset[str] = DEFAULT_APPROVAL_REQUIRED_TOOLS
    timeout_seconds: float = 300.0

    def requires_approval(self, tool_name: str) -> bool:
        return tool_name in self.approval_required_tools
