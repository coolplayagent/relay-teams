from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.hooks.hook_models import HookRuntimeSnapshot


class HookRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: HookRuntimeSnapshot
    env: dict[str, str] = Field(default_factory=dict)


class HookRuntimeState:
    def __init__(self) -> None:
        self._runs: dict[str, HookRunState] = {}

    def set_snapshot(self, run_id: str, snapshot: HookRuntimeSnapshot) -> None:
        self._runs[run_id] = HookRunState(snapshot=snapshot)

    def get_snapshot(self, run_id: str) -> HookRuntimeSnapshot | None:
        state = self._runs.get(run_id)
        return None if state is None else state.snapshot

    def set_env(self, run_id: str, env: dict[str, str]) -> None:
        state = self._runs.get(run_id)
        if state is None:
            return
        state.env.update(env)

    def get_env(self, run_id: str) -> dict[str, str]:
        state = self._runs.get(run_id)
        if state is None:
            return {}
        return dict(state.env)

    def clear(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
