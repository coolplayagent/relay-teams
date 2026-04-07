from __future__ import annotations

import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


GateAction = Literal["approve", "revise"]


class _GateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    instance_id: str
    role_id: str
    summary: str
    event: threading.Event = Field(default_factory=threading.Event)
    action: GateAction | None = None
    feedback: str = ""


class GateManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gates: dict[str, dict[str, _GateEntry]] = {}

    def open_gate(
        self,
        run_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        summary: str,
    ) -> None:
        with self._lock:
            self._gates.setdefault(run_id, {})[task_id] = _GateEntry(
                instance_id=instance_id,
                role_id=role_id,
                summary=summary,
            )

    def resolve_gate(
        self,
        run_id: str,
        task_id: str,
        action: GateAction,
        feedback: str = "",
    ) -> None:
        with self._lock:
            entry = self._gates.get(run_id, {}).get(task_id)
        if entry is None:
            raise KeyError(f"No open gate for run={run_id} task={task_id}")
        entry.action = action
        entry.feedback = feedback
        entry.event.set()

    def wait_for_gate(
        self,
        run_id: str,
        task_id: str,
        timeout: float = 300.0,
    ) -> tuple[GateAction, str]:
        with self._lock:
            entry = self._gates.get(run_id, {}).get(task_id)
        if entry is None:
            raise KeyError(f"No gate registered for run={run_id} task={task_id}")
        triggered = entry.event.wait(timeout=timeout)
        if not triggered:
            raise TimeoutError(
                f"Gate timed out after {timeout}s: run={run_id} task={task_id}"
            )
        if entry.action is None:
            raise RuntimeError(
                f"Gate resolved without action: run={run_id} task={task_id}"
            )
        return entry.action, entry.feedback

    def close_gate(self, run_id: str, task_id: str) -> None:
        with self._lock:
            run_gates = self._gates.get(run_id, {})
            run_gates.pop(task_id, None)

    def list_open_gates(self, run_id: str) -> list[dict[str, str]]:
        with self._lock:
            entries = dict(self._gates.get(run_id, {}))
        result: list[dict[str, str]] = []
        for task_id, entry in entries.items():
            if not entry.event.is_set():
                result.append(
                    {
                        "task_id": task_id,
                        "instance_id": entry.instance_id,
                        "role_id": entry.role_id,
                        "summary": entry.summary,
                    }
                )
        return result
