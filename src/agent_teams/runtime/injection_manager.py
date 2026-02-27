from __future__ import annotations

from threading import Lock

from agent_teams.core.enums import InjectionSource
from agent_teams.core.models import InjectionMessage


class RunInjectionManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active_runs: set[str] = set()
        self._queues: dict[str, dict[str, list[InjectionMessage]]] = {}

    def activate(self, run_id: str) -> None:
        with self._lock:
            self._active_runs.add(run_id)
            self._queues.setdefault(run_id, {})

    def deactivate(self, run_id: str) -> None:
        with self._lock:
            self._active_runs.discard(run_id)
            self._queues.pop(run_id, None)

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active_runs

    def enqueue(
        self,
        run_id: str,
        recipient_instance_id: str,
        source: InjectionSource,
        content: str,
        sender_instance_id: str | None = None,
        sender_role_id: str | None = None,
    ) -> InjectionMessage:
        priority = _priority_for(source)
        message = InjectionMessage(
            run_id=run_id,
            recipient_instance_id=recipient_instance_id,
            source=source,
            content=content,
            sender_instance_id=sender_instance_id,
            sender_role_id=sender_role_id,
            priority=priority,
        )
        with self._lock:
            if run_id not in self._active_runs:
                raise KeyError(f'Run is not active: {run_id}')
            per_run = self._queues.setdefault(run_id, {})
            per_run.setdefault(recipient_instance_id, []).append(message)
        return message

    def drain_at_boundary(self, run_id: str, recipient_instance_id: str) -> tuple[InjectionMessage, ...]:
        with self._lock:
            queue = self._queues.get(run_id, {}).get(recipient_instance_id, [])
            if not queue:
                return ()
            ordered = sorted(queue, key=lambda item: (item.priority, item.created_at))
            self._queues.setdefault(run_id, {})[recipient_instance_id] = []
        return tuple(ordered)


def _priority_for(source: InjectionSource) -> int:
    if source == InjectionSource.SYSTEM:
        return 0
    if source == InjectionSource.USER:
        return 1
    return 2
