from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent_teams.core.models import IntentInput, RunResult
from agent_teams.coordination.coordinator import CoordinatorGraph


class MetaAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    coordinator: CoordinatorGraph

    async def handle_intent(
        self, intent: IntentInput, trace_id: str | None = None
    ) -> RunResult:
        trace_id, task_id, status, output = await self.coordinator.run(
            intent, trace_id=trace_id
        )
        return RunResult(
            trace_id=trace_id, root_task_id=task_id, status=status, output=output
        )
