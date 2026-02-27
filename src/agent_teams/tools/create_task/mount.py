from __future__ import annotations

import uuid

from pydantic_ai import Agent

from agent_teams.core.models import TaskEnvelope, VerificationPlan
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import execute_tool

MAX_COORDINATOR_DELEGATED_TASKS = 4


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def create_task(
        ctx,
        objective: str,
        scope: list[str],
        dod: list[str],
        verification_checklist: list[str],
        parent_task_id: str | None = None,
        parent_instruction: str | None = None,
    ) -> str:
        def _action() -> str:
            if ctx.deps.role_id == 'coordinator_agent':
                records = ctx.deps.task_repo.list_by_trace(ctx.deps.trace_id)
                delegated_count = sum(1 for item in records if item.envelope.task_id != ctx.deps.task_id)
                if delegated_count >= MAX_COORDINATOR_DELEGATED_TASKS:
                    raise ValueError(
                        'Coordinator delegated task limit reached for this run '
                        f'({MAX_COORDINATOR_DELEGATED_TASKS}). Wait for existing tasks to finish.'
                    )

            task_id = f"task_{uuid.uuid4().hex[:12]}"
            envelope = TaskEnvelope(
                task_id=task_id,
                session_id=ctx.deps.session_id,
                trace_id=ctx.deps.trace_id,
                parent_task_id=parent_task_id,
                objective=objective,
                parent_instruction=parent_instruction,
                scope=tuple(scope),
                dod=tuple(dod),
                verification=VerificationPlan(checklist=tuple(verification_checklist)),
            )
            ctx.deps.task_repo.create(envelope)
            return envelope.task_id

        return execute_tool(
            ctx,
            tool_name='create_task',
            args_summary={
                'objective_len': len(objective),
                'scope_count': len(scope),
                'dod_count': len(dod),
                'verification_count': len(verification_checklist),
                'parent_task_id': parent_task_id,
                'has_parent_instruction': bool(parent_instruction),
            },
            action=_action,
        )
