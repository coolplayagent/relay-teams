from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal


GateAction = Literal['approve', 'revise']


@dataclass
class _GateEntry:
    instance_id: str
    role_id: str
    summary: str
    event: threading.Event = field(default_factory=threading.Event)
    action: GateAction | None = None
    feedback: str = ''


class GateManager:
    """
    Manages per-task confirmation gates.

    After a subagent completes, the coordinator calls `open_gate()` to pause
    execution.  The HTTP endpoint calls `resolve_gate()` with the human's
    decision.  The coordinator thread is unblocked via `wait_for_gate()`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # { run_id: { task_id: _GateEntry } }
        self._gates: dict[str, dict[str, _GateEntry]] = {}

    def open_gate(
        self,
        run_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        summary: str,
    ) -> None:
        """Register a new gate that blocks until the human resolves it."""
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
        feedback: str = '',
    ) -> None:
        """Called by the HTTP handler when the user clicks Approve or Revise."""
        with self._lock:
            entry = self._gates.get(run_id, {}).get(task_id)
        if entry is None:
            raise KeyError(f'No open gate for run={run_id} task={task_id}')
        entry.action = action
        entry.feedback = feedback
        entry.event.set()

    def wait_for_gate(
        self,
        run_id: str,
        task_id: str,
        timeout: float = 300.0,
    ) -> tuple[GateAction, str]:
        """
        Block the calling thread until the gate is resolved (or times out).
        Returns (action, feedback).  Raises TimeoutError on timeout.
        """
        with self._lock:
            entry = self._gates.get(run_id, {}).get(task_id)
        if entry is None:
            raise KeyError(f'No gate registered for run={run_id} task={task_id}')
        triggered = entry.event.wait(timeout=timeout)
        if not triggered:
            raise TimeoutError(f'Gate timed out after {timeout}s: run={run_id} task={task_id}')
        return entry.action, entry.feedback  # type: ignore[return-value]

    def close_gate(self, run_id: str, task_id: str) -> None:
        """Remove a gate entry after it has been resolved."""
        with self._lock:
            run_gates = self._gates.get(run_id, {})
            run_gates.pop(task_id, None)

    def list_open_gates(self, run_id: str) -> list[dict]:
        """Return metadata about currently open gates for a run (for API use)."""
        with self._lock:
            entries = dict(self._gates.get(run_id, {}))
        result = []
        for task_id, entry in entries.items():
            if not entry.event.is_set():
                result.append({
                    'task_id': task_id,
                    'instance_id': entry.instance_id,
                    'role_id': entry.role_id,
                    'summary': entry.summary,
                })
        return result
