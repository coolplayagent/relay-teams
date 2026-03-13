# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.agents.subagent import SubAgentRequest, SubAgentRunner
from agent_teams.agents.execution.runtime_prompts import (
    PromptBuildInput,
    RuntimePromptBuilder,
)
from agent_teams.roles.models import RoleDefinition
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


class _CapturingProvider:
    def __init__(self) -> None:
        self.request: SubAgentRequest | None = None

    async def generate(self, request: object) -> str:
        assert isinstance(request, SubAgentRequest)
        self.request = request
        return "done"


class _FixedPromptBuilder(RuntimePromptBuilder):
    def build(self, data: PromptBuildInput) -> str:
        return (
            f"role={data.role.role_id};task={data.task.task_id};"
            f"shared={data.shared_state_snapshot[0][0]}"
        )


@pytest.mark.asyncio
async def test_subagent_runner_builds_runtime_request() -> None:
    provider = _CapturingProvider()
    runner = SubAgentRunner(
        role=RoleDefinition(
            role_id="researcher",
            name="Researcher",
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
