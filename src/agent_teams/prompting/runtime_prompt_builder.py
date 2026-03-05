from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent_teams.core.models import RoleDefinition, TaskEnvelope


class PromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: RoleDefinition
    task: TaskEnvelope
    shared_state_snapshot: tuple[tuple[str, str], ...]


class RuntimePromptBuilder:
    def build(self, data: PromptBuildInput) -> str:
        state_lines = "\n".join(f"- {k}: {v}" for k, v in data.shared_state_snapshot)
        runtime_contract = ""
        if data.role.role_id == "coordinator_agent":
            runtime_contract = (
                "RuntimeContract:\n"
                "- A coordinator turn can call tools many times, but delegated tasks run after the turn ends.\n"
                "- Do not claim task started/completed without get_workflow_status evidence.\n"
                "- Prefer workflow tools over raw task-by-task creation.\n\n"
            )
        return (
            f"{data.role.system_prompt}\n\n"
            f"{runtime_contract}"
            f"TaskRef: {data.task.task_id}\n"
            f"Objective: {data.task.objective}\n"
            f"SharedState:\n{state_lines if state_lines else '- none'}"
        )
