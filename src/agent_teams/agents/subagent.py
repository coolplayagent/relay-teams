from __future__ import annotations

from dataclasses import dataclass

from agent_teams.core.models import RoleDefinition, TaskEnvelope
from agent_teams.prompting.runtime_prompt_builder import PromptBuildInput, RuntimePromptBuilder
from agent_teams.providers.llm import LLMProvider, LLMRequest


@dataclass
class SubAgentRunner:
    role: RoleDefinition
    prompt_builder: RuntimePromptBuilder
    provider: LLMProvider

    def run(
        self,
        task: TaskEnvelope,
        instance_id: str,
        parent_instruction: str | None,
        shared_state_snapshot: tuple[tuple[str, str], ...],
    ) -> str:
        system_prompt = self.prompt_builder.build(
            PromptBuildInput(
                role=self.role,
                task=task,
                parent_instruction=parent_instruction,
                shared_state_snapshot=shared_state_snapshot,
            )
        )
        return self.provider.generate(
            LLMRequest(
                run_id=task.trace_id,
                trace_id=task.trace_id,
                task_id=task.task_id,
                session_id=task.session_id,
                instance_id=instance_id,
                role_id=self.role.role_id,
                system_prompt=system_prompt,
                user_prompt=task.objective,
            )
        )
