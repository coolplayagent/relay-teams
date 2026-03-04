from __future__ import annotations

import json
import uuid
from typing import cast

from agent_teams.application.rounds_projection import (
    build_session_rounds,
    find_round_by_run_id,
    paginate_rounds,
)
from agent_teams.core.enums import ScopeType
from agent_teams.core.models import AgentRuntimeRecord, ScopeRef, SessionRecord
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository


class SessionService:
    def __init__(
        self,
        *,
        session_repo: SessionRepository,
        task_repo: TaskRepository,
        agent_repo: AgentInstanceRepository,
        shared_store: SharedStore,
        message_repo: MessageRepository,
        event_log: EventLog,
    ) -> None:
        self._session_repo: SessionRepository = session_repo
        self._task_repo: TaskRepository = task_repo
        self._agent_repo: AgentInstanceRepository = agent_repo
        self._shared_store: SharedStore = shared_store
        self._message_repo: MessageRepository = message_repo
        self._event_log: EventLog = event_log

    def create_session(
        self,
        *,
        session_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        if not session_id:
            session_id = f"session-{uuid.uuid4().hex[:8]}"
        return self._session_repo.create(session_id=session_id, metadata=metadata)

    def update_session(self, session_id: str, metadata: dict[str, str]) -> None:
        self._session_repo.update_metadata(session_id, metadata)

    def delete_session(self, session_id: str) -> None:
        _ = self._session_repo.get(session_id)

        tasks = self._task_repo.list_by_session(session_id)
        agents = self._agent_repo.list_by_session(session_id)
        task_ids = [t.envelope.task_id for t in tasks]
        instance_ids = [a.instance_id for a in agents]

        self._shared_store.delete_by_session(session_id, task_ids, instance_ids)
        self._message_repo.delete_by_session(session_id)
        self._event_log.delete_by_session(session_id)
        self._task_repo.delete_by_session(session_id)
        self._agent_repo.delete_by_session(session_id)
        self._session_repo.delete(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        return self._session_repo.get(session_id)

    def list_sessions(self) -> tuple[SessionRecord, ...]:
        return self._session_repo.list_all()

    def list_agents_in_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        return self._agent_repo.list_by_session(session_id)

    def get_agent_messages(
        self, session_id: str, instance_id: str
    ) -> list[dict[str, object]]:
        return cast(
            list[dict[str, object]],
            self._message_repo.get_messages_for_instance(session_id, instance_id),
        )

    def get_global_events(self, session_id: str) -> list[dict[str, object]]:
        events = self._event_log.list_by_session(session_id)
        return cast(list[dict[str, object]], list(events))

    def get_session_messages(self, session_id: str) -> list[dict[str, object]]:
        return cast(
            list[dict[str, object]],
            self._message_repo.get_messages_by_session(session_id),
        )

    def get_session_workflows(self, session_id: str) -> list[dict[str, object]]:
        workflows: list[dict[str, object]] = []
        tasks = self._task_repo.list_by_session(session_id)
        for task in tasks:
            scope = ScopeRef(scope_type=ScopeType.TASK, scope_id=task.envelope.task_id)
            obj = self._shared_store.get_state(scope, "workflow_graph")
            if obj:
                workflows.append(cast(dict[str, object], json.loads(obj)))
        return workflows

    def build_session_rounds(self, session_id: str) -> list[dict[str, object]]:
        return build_session_rounds(
            session_id=session_id,
            event_log=self._event_log,
            agent_repo=self._agent_repo,
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            get_session_messages=self.get_session_messages,
        )

    def get_session_rounds(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
    ) -> dict[str, object]:
        rounds = self.build_session_rounds(session_id)
        return paginate_rounds(
            rounds,
            limit=limit,
            cursor_run_id=cursor_run_id,
        )

    def get_round(self, session_id: str, run_id: str) -> dict[str, object]:
        rounds = self.build_session_rounds(session_id)
        return find_round_by_run_id(rounds, session_id=session_id, run_id=run_id)
