# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import is_coordinator_role_definition
from agent_teams.workflow.models import TaskEnvelope


class RuntimePromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: RoleDefinition
    task: TaskEnvelope
    shared_state_snapshot: tuple[tuple[str, str], ...]


def build_runtime_system_prompt(data: RuntimePromptBuildInput) -> str:
    sections: list[str] = [f"## Role\n{data.role.system_prompt}"]
    if is_coordinator_role_definition(data.role):
        sections.append(
            "## Runtime Contract\n"
            "- A coordinator turn can call tools many times, but delegated task execution remains explicit.\n"
            "- Use list_run_tasks and dispatch_task results as the source of truth for progress and outputs.\n"
            "- Create tasks only when delegation is necessary; otherwise answer directly."
        )
    sections.append(f"## Task Context\n- TaskRef: {data.task.task_id}")
    shared_state_lines = (
        "\n".join(f"- {key}: {value}" for key, value in data.shared_state_snapshot)
        if data.shared_state_snapshot
        else "- none"
    )
    sections.append(f"## Shared State\n{shared_state_lines}")
    return "\n\n".join(sections)
