# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.state.run_runtime_repo import RunRuntimeRepository


class ActiveSessionRunRegistry:
    def __init__(
        self,
        *,
        run_runtime_repo: RunRuntimeRepository | None = None,
    ) -> None:
        self._run_runtime_repo = run_runtime_repo
        self._active_run_by_session: dict[str, str] = {}
        self.hydrate()

    def hydrate(self) -> None:
        if self._run_runtime_repo is None:
            return
        self._active_run_by_session.clear()
        for runtime in sorted(
            self._run_runtime_repo.list_recoverable(),
            key=lambda item: item.updated_at,
            reverse=True,
        ):
            if runtime.session_id not in self._active_run_by_session:
                self._active_run_by_session[runtime.session_id] = runtime.run_id

    def get_active_run_id(self, session_id: str) -> str | None:
        return self._active_run_by_session.get(session_id)

    def remember_active_run(self, *, session_id: str, run_id: str) -> None:
        self._active_run_by_session[session_id] = run_id

    def drop_active_run(self, *, session_id: str, run_id: str) -> None:
        if self._active_run_by_session.get(session_id) == run_id:
            self._active_run_by_session.pop(session_id, None)
