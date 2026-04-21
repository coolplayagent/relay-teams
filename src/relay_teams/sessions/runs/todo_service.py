# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
from json import dumps

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.todo_models import (
    TodoItem,
    TodoSnapshot,
    TodoStatus,
    build_todo_snapshot,
    empty_todo_snapshot,
)
from relay_teams.sessions.runs.todo_repository import TodoRepository

MAX_TODO_ITEMS = 50
MAX_TODO_TOTAL_CHARS = 16_000


class TodoService:
    def __init__(
        self,
        *,
        repository: TodoRepository,
        run_event_hub: RunEventHub | None = None,
    ) -> None:
        self._repository = repository
        self._run_event_hub = run_event_hub

    def get_for_run(
        self,
        *,
        run_id: str,
        session_id: str,
    ) -> TodoSnapshot:
        snapshot = self._repository.get(run_id)
        if snapshot is not None:
            return snapshot
        return empty_todo_snapshot(run_id=run_id, session_id=session_id)

    def list_for_session(self, session_id: str) -> tuple[TodoSnapshot, ...]:
        return self._repository.list_by_session(session_id)

    def delete_for_session(self, session_id: str) -> None:
        self._repository.delete_by_session(session_id)

    def delete_for_run(self, run_id: str) -> None:
        self._repository.delete_by_run(run_id)

    def replace_for_run(
        self,
        *,
        run_id: str,
        session_id: str,
        items: Sequence[TodoItem],
        updated_by_role_id: str | None = None,
        updated_by_instance_id: str | None = None,
    ) -> TodoSnapshot:
        normalized_items = tuple(items)
        _validate_items(normalized_items)
        previous = self._repository.get(run_id)
        next_version = 1 if previous is None else previous.version + 1
        snapshot = build_todo_snapshot(
            run_id=run_id,
            session_id=session_id,
            items=normalized_items,
            version=next_version,
            updated_by_role_id=updated_by_role_id,
            updated_by_instance_id=updated_by_instance_id,
        )
        persisted = self._repository.upsert(snapshot)
        self._publish_todo_updated_event(persisted)
        return persisted

    def clear_for_run(
        self,
        *,
        run_id: str,
        session_id: str,
        updated_by_role_id: str | None = None,
        updated_by_instance_id: str | None = None,
    ) -> TodoSnapshot:
        return self.replace_for_run(
            run_id=run_id,
            session_id=session_id,
            items=(),
            updated_by_role_id=updated_by_role_id,
            updated_by_instance_id=updated_by_instance_id,
        )

    def _publish_todo_updated_event(self, snapshot: TodoSnapshot) -> None:
        if self._run_event_hub is None:
            return
        self._run_event_hub.publish(
            RunEvent(
                session_id=snapshot.session_id,
                run_id=snapshot.run_id,
                trace_id=snapshot.run_id,
                instance_id=snapshot.updated_by_instance_id,
                role_id=snapshot.updated_by_role_id,
                event_type=RunEventType.TODO_UPDATED,
                payload_json=dumps(
                    snapshot.model_dump(mode="json"), ensure_ascii=False
                ),
            )
        )


def _validate_items(items: Sequence[TodoItem]) -> None:
    if len(items) > MAX_TODO_ITEMS:
        raise ValueError(f"Todo list exceeds maximum item count of {MAX_TODO_ITEMS}")
    in_progress_count = sum(
        1 for item in items if item.status == TodoStatus.IN_PROGRESS
    )
    if in_progress_count > 1:
        raise ValueError("Todo list can contain at most one in_progress item")
    total_chars = sum(len(item.content) for item in items)
    if total_chars > MAX_TODO_TOTAL_CHARS:
        raise ValueError(
            f"Todo list exceeds maximum content size of {MAX_TODO_TOTAL_CHARS} characters"
        )
