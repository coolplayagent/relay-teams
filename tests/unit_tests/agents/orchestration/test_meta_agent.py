# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.agents.orchestration.meta_agent import MetaAgent
from agent_teams.sessions.runs.run_models import IntentInput


class _CoordinatorStub:
    def __init__(self) -> None:
        self.run_calls: list[tuple[IntentInput, str | None]] = []
        self.resume_calls: list[str] = []

    async def run(
        self,
        intent: IntentInput,
        *,
        trace_id: str | None = None,
    ) -> tuple[str, str, str, str]:
        self.run_calls.append((intent, trace_id))
        return ("trace-1", "task-1", "completed", "delegated")

    async def resume(self, *, trace_id: str) -> tuple[str, str, str, str]:
        self.resume_calls.append(trace_id)
        return (trace_id, "task-2", "completed", "resumed")


@pytest.mark.asyncio
async def test_handle_intent_delegates_to_coordinator() -> None:
    coordinator = _CoordinatorStub()
    meta_agent = MetaAgent.model_construct(coordinator=coordinator)
    intent = IntentInput(session_id="session-1", intent="plan this")

    result = await meta_agent.handle_intent(intent, trace_id="trace-in")

    assert coordinator.run_calls == [(intent, "trace-in")]
    assert result.trace_id == "trace-1"
    assert result.root_task_id == "task-1"
    assert result.status == "completed"
    assert result.output == "delegated"


@pytest.mark.asyncio
async def test_resume_run_delegates_to_coordinator() -> None:
    coordinator = _CoordinatorStub()
    meta_agent = MetaAgent.model_construct(coordinator=coordinator)

    result = await meta_agent.resume_run(trace_id="trace-resume")

    assert coordinator.resume_calls == ["trace-resume"]
    assert result.trace_id == "trace-resume"
    assert result.root_task_id == "task-2"
    assert result.status == "completed"
    assert result.output == "resumed"
