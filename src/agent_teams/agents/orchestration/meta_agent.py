# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent_teams.agents.orchestration.coordinator import CoordinatorGraph
from agent_teams.sessions.runs.run_models import IntentInput, RunResult


class MetaAgent(BaseModel):
    """Intent dispatcher that forwards user intent into the coordination layer."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    coordinator: CoordinatorGraph

    async def handle_intent(
        self, intent: IntentInput, trace_id: str | None = None
    ) -> RunResult:
        next_trace_id, task_id, status, output = await self.coordinator.run(
            intent, trace_id=trace_id
        )
        return RunResult(
            trace_id=next_trace_id,
            root_task_id=task_id,
            status=status,
            output=output,
        )

    async def resume_run(self, *, trace_id: str) -> RunResult:
        next_trace_id, task_id, status, output = await self.coordinator.resume(
            trace_id=trace_id
        )
        return RunResult(
            trace_id=next_trace_id,
            root_task_id=task_id,
            status=status,
            output=output,
        )
