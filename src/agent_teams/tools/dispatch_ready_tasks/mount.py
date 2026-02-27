from __future__ import annotations

import json

from pydantic_ai import Agent

from agent_teams.core.enums import InstanceStatus, TaskStatus
from agent_teams.core.models import TaskRecord
from agent_teams.tools.materialize_code_shards_from_design.mount import materialize_code_shards_from_design_impl
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.workflow.runtime_graph import load_graph

STAGE_ROLE = {
    'spec': 'spec_builder',
    'design': 'design_builder',
    'code': 'coder',
    'verify': 'verify',
}


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def dispatch_ready_tasks(ctx, workflow_id: str, max_dispatch: int = 4) -> str:
        def _action() -> str:
            graph = load_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id)
            if graph is None:
                raise KeyError('workflow_graph not found, call create_workflow_graph first')
            if graph.get('workflow_id') != workflow_id:
                raise ValueError(f'workflow_id mismatch: expected {graph.get("workflow_id")}, got {workflow_id}')

            records = {record.envelope.task_id: record for record in ctx.deps.task_repo.list_by_trace(ctx.deps.trace_id)}
            stages = graph.get('stages', {})
            if not isinstance(stages, dict):
                raise ValueError('invalid workflow graph stages')

            bounded_dispatch = max(1, min(int(max_dispatch), 8))
            max_code_parallel = int(graph.get('parallel_limit', 4))

            dispatched: list[dict[str, str]] = []
            executed: list[dict[str, str]] = []
            failed: list[dict[str, str]] = []

            def _refresh_records() -> dict[str, TaskRecord]:
                return {record.envelope.task_id: record for record in ctx.deps.task_repo.list_by_trace(ctx.deps.trace_id)}

            def _ensure_and_execute(task_id: str, stage: str) -> bool:
                nonlocal records
                if len(dispatched) >= bounded_dispatch:
                    return False
                record = records.get(task_id)
                if record is None:
                    return False
                if record.status != TaskStatus.CREATED:
                    return False
                instance = ctx.deps.instance_pool.create_subagent(STAGE_ROLE[stage])
                ctx.deps.agent_repo.upsert_instance(
                    run_id=ctx.deps.run_id,
                    trace_id=ctx.deps.trace_id,
                    session_id=ctx.deps.session_id,
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    status=InstanceStatus.IDLE,
                )
                ctx.deps.task_repo.update_status(
                    task_id=task_id,
                    status=TaskStatus.ASSIGNED,
                    assigned_instance_id=instance.instance_id,
                )
                dispatched.append({'task_id': task_id, 'role_id': STAGE_ROLE[stage], 'instance_id': instance.instance_id})
                try:
                    ctx.deps.task_execution_service.execute(
                        instance_id=instance.instance_id,
                        role_id=instance.role_id,
                        task=record.envelope,
                    )
                    executed.append({'task_id': task_id, 'stage': stage, 'status': 'completed'})
                except Exception as exc:
                    failed.append({'task_id': task_id, 'stage': stage, 'status': 'failed', 'error': str(exc)})
                records = _refresh_records()
                return True

            converged_stage = 'blocked'
            progressed = True
            while progressed:
                progressed = False

                spec_id = str(stages.get('spec', {}).get('task_id', ''))
                design_id = str(stages.get('design', {}).get('task_id', ''))
                verify_id = str(stages.get('verify', {}).get('task_id', ''))

                spec_record = records.get(spec_id)
                if spec_record is not None and spec_record.status == TaskStatus.CREATED:
                    progressed = _ensure_and_execute(spec_id, 'spec') or progressed
                    converged_stage = 'spec_executed'
                    continue
                if spec_record is None or spec_record.status != TaskStatus.COMPLETED:
                    converged_stage = 'waiting_spec'
                    break

                design_record = records.get(design_id)
                if design_record is not None and design_record.status == TaskStatus.CREATED:
                    progressed = _ensure_and_execute(design_id, 'design') or progressed
                    converged_stage = 'design_executed'
                    continue
                if design_record is None or design_record.status != TaskStatus.COMPLETED:
                    converged_stage = 'waiting_design'
                    break

                if not bool(graph.get('code_materialized')):
                    materialized = materialize_code_shards_from_design_impl(
                        ctx.deps,
                        root_task_id=ctx.deps.task_id,
                        workflow_id=workflow_id,
                        parallel_limit=max_code_parallel,
                    )
                    graph = load_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id) or graph
                    stages = graph.get('stages', stages) if isinstance(graph.get('stages', {}), dict) else stages
                    records = _refresh_records()
                    progressed = True
                    converged_stage = f'code_materialized:{materialized.get("mode", "parallel")}'
                    continue

                code_items = [item for item in graph.get('code_tasks', []) if isinstance(item, dict)]
                code_created_ids: list[str] = []
                code_failed = 0
                for item in code_items:
                    code_id = str(item.get('task_id', ''))
                    if not code_id:
                        continue
                    code_record = records.get(code_id)
                    if code_record is None:
                        continue
                    if code_record.status == TaskStatus.CREATED:
                        code_created_ids.append(code_id)
                    elif code_record.status in (TaskStatus.FAILED, TaskStatus.TIMEOUT):
                        code_failed += 1

                for code_id in code_created_ids[:max_code_parallel]:
                    progressed = _ensure_and_execute(code_id, 'code') or progressed

                records = _refresh_records()
                if code_failed > 0 or any(
                    rec.status in (TaskStatus.FAILED, TaskStatus.TIMEOUT)
                    for rec in (records.get(str(item.get('task_id', ''))) for item in code_items)
                    if rec is not None
                ):
                    converged_stage = 'code_partial_failed'
                    break

                if any(
                    rec is not None and rec.status != TaskStatus.COMPLETED
                    for rec in (records.get(str(item.get('task_id', ''))) for item in code_items)
                ):
                    converged_stage = 'waiting_code'
                    if not progressed:
                        break
                    continue

                verify_record = records.get(verify_id)
                if verify_record is not None and verify_record.status == TaskStatus.CREATED:
                    progressed = _ensure_and_execute(verify_id, 'verify') or progressed
                    converged_stage = 'verify_executed'
                    continue
                if verify_record is not None and verify_record.status == TaskStatus.COMPLETED:
                    converged_stage = 'verify_completed'
                else:
                    converged_stage = 'waiting_verify'
                break

            return json.dumps(
                {
                    'ok': True,
                    'workflow_id': workflow_id,
                    'dispatched': dispatched,
                    'executed': executed,
                    'failed': failed,
                    'converged_stage': converged_stage,
                    'remaining_budget': max(0, bounded_dispatch - len(dispatched)),
                    'code_batch': {
                        'total': len([item for item in graph.get('code_tasks', []) if isinstance(item, dict)]),
                        'succeeded': len([item for item in executed if item.get('stage') == 'code']),
                        'failed': len([item for item in failed if item.get('stage') == 'code']),
                    },
                },
                ensure_ascii=False,
            )

        return execute_tool(
            ctx,
            tool_name='dispatch_ready_tasks',
            args_summary={'workflow_id': workflow_id, 'max_dispatch': max_dispatch},
            action=_action,
        )
