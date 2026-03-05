from __future__ import annotations

import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ToolApprovalAction = Literal["approve", "deny"]


class _ToolApprovalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    instance_id: str
    role_id: str
    tool_name: str
    args_preview: str
    risk_level: str
    event: threading.Event = Field(default_factory=threading.Event)
    action: ToolApprovalAction | None = None
    feedback: str = ""


class ToolApprovalManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._approvals: dict[str, dict[str, _ToolApprovalEntry]] = {}

    def open_approval(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        instance_id: str,
        role_id: str,
        tool_name: str,
        args_preview: str,
        risk_level: str = "medium",
    ) -> None:
        with self._lock:
            self._approvals.setdefault(run_id, {})[tool_call_id] = _ToolApprovalEntry(
                instance_id=instance_id,
                role_id=role_id,
                tool_name=tool_name,
                args_preview=args_preview,
                risk_level=risk_level,
            )

    def resolve_approval(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        action: ToolApprovalAction,
        feedback: str = "",
    ) -> None:
        with self._lock:
            entry = self._approvals.get(run_id, {}).get(tool_call_id)
        if entry is None:
            raise KeyError(
                f"No open tool approval for run={run_id} tool_call_id={tool_call_id}"
            )
        entry.action = action
        entry.feedback = feedback
        entry.event.set()

    def wait_for_approval(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        timeout: float = 300.0,
    ) -> tuple[ToolApprovalAction, str]:
        with self._lock:
            entry = self._approvals.get(run_id, {}).get(tool_call_id)
        if entry is None:
            raise KeyError(
                f"No tool approval registered for run={run_id} tool_call_id={tool_call_id}"
            )
        triggered = entry.event.wait(timeout=timeout)
        if not triggered:
            raise TimeoutError(
                f"Tool approval timed out after {timeout}s: run={run_id} tool_call_id={tool_call_id}"
            )
        if entry.action is None:
            raise RuntimeError(
                f"Tool approval resolved without action: run={run_id} tool_call_id={tool_call_id}"
            )
        return entry.action, entry.feedback

    def close_approval(self, *, run_id: str, tool_call_id: str) -> None:
        with self._lock:
            run_approvals = self._approvals.get(run_id, {})
            run_approvals.pop(tool_call_id, None)

    def list_open_approvals(self, *, run_id: str) -> list[dict[str, str]]:
        with self._lock:
            entries = dict(self._approvals.get(run_id, {}))
        result: list[dict[str, str]] = []
        for tool_call_id, entry in entries.items():
            if not entry.event.is_set():
                result.append(
                    {
                        "tool_call_id": tool_call_id,
                        "instance_id": entry.instance_id,
                        "role_id": entry.role_id,
                        "tool_name": entry.tool_name,
                        "args_preview": entry.args_preview,
                        "risk_level": entry.risk_level,
                    }
                )
        return result
