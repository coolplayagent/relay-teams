# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict

from agent_teams.agents.execution.system_prompts import (
    PromptBuildInput,
    RuntimePromptBuilder,
)
from agent_teams.agents.tasks.models import TaskEnvelope
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.sessions.runs.run_models import RunThinkingConfig


class SubAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    workspace_id: str
    conversation_id: str
    instance_id: str
    role_id: str
    system_prompt: str
    user_prompt: str | None
    thinking: RunThinkingConfig = RunThinkingConfig()


class SubAgentRunner(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role: RoleDefinition
    prompt_builder: RuntimePromptBuilder
    provider: object

    async def run(
        self,
        task: TaskEnvelope,
        instance_id: str,
        workspace_id: str,
        working_directory: Path | None,
        conversation_id: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        thinking: RunThinkingConfig | None = None,
        system_prompt_override: str | None = None,
    ) -> str:
        system_prompt = system_prompt_override
        if system_prompt is None:
            system_prompt = await self.prompt_builder.build(
                PromptBuildInput(
                    role=self.role,
                    task=task,
                    shared_state_snapshot=shared_state_snapshot,
                    working_directory=working_directory,
                )
            )
        generate = cast(
            Callable[[object], Awaitable[str]],
            getattr(self.provider, "generate"),
        )
        return await generate(
            SubAgentRequest(
                run_id=task.trace_id,
                trace_id=task.trace_id,
                task_id=task.task_id,
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                instance_id=instance_id,
                role_id=self.role.role_id,
                system_prompt=system_prompt,
                user_prompt=None,
                thinking=thinking or RunThinkingConfig(),
            )
        )
