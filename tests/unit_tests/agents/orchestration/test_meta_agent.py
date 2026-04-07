# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.orchestration.meta_agent import MetaAgent
from relay_teams.agents.orchestration.coordinator import CoordinatorRunResult
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.run_models import IntentInput


class _CoordinatorStub:
    def __init__(self) -> None:
        self.run_calls: list[tuple[IntentInput, str | None]] = []
        self.resume_calls: list[str] = []

    async def run(
        self,
        intent: IntentInput,
        *,
        trace_id: str | None = None,
    ) -> CoordinatorRunResult:
        self.run_calls.append((intent, trace_id))
        return CoordinatorRunResult(
            trace_id="trace-1",
            root_task_id="task-1",
            output="delegated",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
        )

    async def resume(self, *, trace_id: str) -> CoordinatorRunResult:
        self.resume_calls.append(trace_id)
        return CoordinatorRunResult(
            trace_id=trace_id,
            root_task_id="task-2",
            output="resumed",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
        )


@pytest.mark.asyncio
async def test_handle_intent_delegates_to_coordinator() -> None:
    coordinator = _CoordinatorStub()
    meta_agent = MetaAgent.model_construct(coordinator=coordinator)
    intent = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("plan this"),
    )

    result = await meta_agent.handle_intent(intent, trace_id="trace-in")

    assert coordinator.run_calls == [(intent, "trace-in")]
    assert result.trace_id == "trace-1"
    assert result.root_task_id == "task-1"
    assert result.status == "completed"
    assert result.output_text == "delegated"


@pytest.mark.asyncio
async def test_resume_run_delegates_to_coordinator() -> None:
    coordinator = _CoordinatorStub()
    meta_agent = MetaAgent.model_construct(coordinator=coordinator)

    result = await meta_agent.resume_run(trace_id="trace-resume")

    assert coordinator.resume_calls == ["trace-resume"]
    assert result.trace_id == "trace-resume"
    assert result.root_task_id == "task-2"
    assert result.status == "completed"
    assert result.output_text == "resumed"
