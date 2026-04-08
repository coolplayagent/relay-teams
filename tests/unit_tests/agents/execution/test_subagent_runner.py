# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.agents.execution import SubAgentRequest, SubAgentRunner
from relay_teams.agents.execution.system_prompts import (
    PromptBuildInput,
    RuntimePromptBuilder,
)
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.roles.role_models import RoleDefinition


class _CapturingProvider:
    def __init__(self) -> None:
        self.request: SubAgentRequest | None = None

    async def generate(self, request: object) -> str:
        assert isinstance(request, SubAgentRequest)
        self.request = request
        return "done"


class _FixedPromptBuilder(RuntimePromptBuilder):
    async def build(self, data: PromptBuildInput) -> str:
        task = data.task
        assert task is not None
        assert data.working_directory == Path("/tmp/workspace-root")
        return (
            f"role={data.role.role_id};task={task.task_id};"
            f"shared={data.shared_state_snapshot[0][0]}"
        )


@pytest.mark.asyncio
async def test_subagent_runner_builds_runtime_request() -> None:
    provider = _CapturingProvider()
    runner = SubAgentRunner(
        role=RoleDefinition(
            role_id="researcher",
            name="Researcher",
            description="Researches implementation details.",
            version="1",
            system_prompt="You are a researcher.",
        ),
        prompt_builder=_FixedPromptBuilder(),
        provider=provider,
    )
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        role_id="researcher",
        objective="Investigate the issue.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )

    result = await runner.run(
        task=task,
        instance_id="instance-1",
        workspace_id="workspace-1",
        working_directory=Path("/tmp/workspace-root"),
        conversation_id="conversation-1",
        shared_state_snapshot=(("context", "available"),),
    )

    assert result == "done"
    assert provider.request == SubAgentRequest(
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id="researcher",
        system_prompt="role=researcher;task=task-1;shared=context",
        user_prompt=None,
    )
