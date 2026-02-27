from __future__ import annotations

from dataclasses import dataclass

from agent_teams.core.models import RoleDefinition, TaskEnvelope


@dataclass(frozen=True)
class PromptBuildInput:
    role: RoleDefinition
    task: TaskEnvelope
    parent_instruction: str | None
    shared_state_snapshot: tuple[tuple[str, str], ...]


class RuntimePromptBuilder:
    def build(self, data: PromptBuildInput) -> str:
        state_lines = '\n'.join(f'- {k}: {v}' for k, v in data.shared_state_snapshot)
        parent = data.parent_instruction or 'N/A'
        runtime_contract = ''
        if data.role.role_id == 'coordinator_agent':
            runtime_contract = (
                'RuntimeContract:\n'
                '- A coordinator turn can call tools many times, but delegated tasks run after the turn ends.\n'
                '- Do not claim task started/completed without query_task/get_workflow_status evidence.\n'
                '- Prefer workflow tools over raw task-by-task creation.\n\n'
            )
        return (
            f'{data.role.system_prompt}\n\n'
            f'{runtime_contract}'
            f'ParentInstruction:\n{parent}\n\n'
            f'TaskRef: {data.task.task_id}\n'
            f'Objective: {data.task.objective}\n'
            f'Scope: {", ".join(data.task.scope)}\n'
            f'DoD: {", ".join(data.task.dod)}\n'
            f'SharedState:\n{state_lines if state_lines else "- none"}'
        )
