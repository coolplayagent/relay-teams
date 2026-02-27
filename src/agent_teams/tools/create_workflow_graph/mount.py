from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic_ai import Agent

from agent_teams.core.models import TaskEnvelope, VerificationPlan
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.workflow.runtime_graph import load_graph, save_graph, stage_tag, workflow_tag


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def create_workflow_graph(
        ctx,
        workflow_type: Literal['spec_flow'],
        objective: str,
        parent_instruction: str | None = None,
        code_parallel_limit: int = 4,
    ) -> str:
        def _action() -> str:
            existing = load_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id)
            if existing is not None:
                return json.dumps(
                    {
                        'ok': True,
                        'created': False,
                        'workflow_id': existing.get('workflow_id'),
                        'trace_id': ctx.deps.trace_id,
                        'stages': existing.get('stages', {}),
                    },
                    ensure_ascii=False,
                )

            workflow_id = f'workflow_{uuid4().hex[:8]}'
            bounded_parallel = max(1, min(int(code_parallel_limit), 8))

            spec_task_id = f'task_{uuid4().hex[:12]}'
            design_task_id = f'task_{uuid4().hex[:12]}'
            verify_task_id = f'task_{uuid4().hex[:12]}'

            common_scope = (workflow_tag(workflow_id),)
            parent_id = ctx.deps.task_id

            spec_task = TaskEnvelope(
                task_id=spec_task_id,
                session_id=ctx.deps.session_id,
                trace_id=ctx.deps.trace_id,
                parent_task_id=parent_id,
                objective=f'[Demo Task] Build requirement spec for: {objective}',
                parent_instruction=parent_instruction
                or 'This is a demo workflow task. Produce a complete spec and publish via write_stage_doc.',
                scope=common_scope + (stage_tag('spec'),),
                dod=('spec_document_written', 'acceptance_criteria_defined', 'non_empty_response'),
                verification=VerificationPlan(checklist=('non_empty_response',)),
            )
            design_task = TaskEnvelope(
                task_id=design_task_id,
                session_id=ctx.deps.session_id,
                trace_id=ctx.deps.trace_id,
                parent_task_id=spec_task_id,
                objective=f'Design technical approach for: {objective}',
                parent_instruction='Read spec stage output and produce design document with MODULE_PLAN json block.',
                scope=common_scope + (stage_tag('design'),),
                dod=('design_document_written', 'module_plan_defined', 'non_empty_response'),
                verification=VerificationPlan(checklist=('non_empty_response',)),
            )
            verify_task = TaskEnvelope(
                task_id=verify_task_id,
                session_id=ctx.deps.session_id,
                trace_id=ctx.deps.trace_id,
                parent_task_id=design_task_id,
                objective=f'Verify implementation quality for: {objective}',
                parent_instruction='Validate final implementation against design/spec and publish verification doc.',
                scope=common_scope + (stage_tag('verify'),),
                dod=('verification_document_written', 'pass_fail_decision', 'non_empty_response'),
                verification=VerificationPlan(checklist=('non_empty_response',)),
            )

            ctx.deps.task_repo.create(spec_task)
            ctx.deps.task_repo.create(design_task)
            ctx.deps.task_repo.create(verify_task)

            graph: dict[str, object] = {
                'workflow_id': workflow_id,
                'workflow_type': workflow_type,
                'objective': objective,
                'trace_id': ctx.deps.trace_id,
                'session_id': ctx.deps.session_id,
                'parallel_limit': bounded_parallel,
                'code_materialized': False,
                'code_mode': 'pending',
                'stages': {
                    'spec': {'task_id': spec_task_id, 'depends_on': []},
                    'design': {'task_id': design_task_id, 'depends_on': [spec_task_id]},
                    'verify': {'task_id': verify_task_id, 'depends_on': []},
                },
                'code_tasks': [],
            }
            save_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id, graph=graph)
            return json.dumps(
                {
                    'ok': True,
                    'created': True,
                    'workflow_id': workflow_id,
                    'trace_id': ctx.deps.trace_id,
                    'stages': {
                        'spec': {'task_id': spec_task_id},
                        'design': {'task_id': design_task_id},
                        'code': {'task_group_id': f'{workflow_id}:code'},
                        'verify': {'task_id': verify_task_id},
                    },
                },
                ensure_ascii=False,
            )

        return execute_tool(
            ctx,
            tool_name='create_workflow_graph',
            args_summary={
                'workflow_type': workflow_type,
                'objective_len': len(objective),
                'has_parent_instruction': bool(parent_instruction),
                'code_parallel_limit': code_parallel_limit,
            },
            action=_action,
        )
