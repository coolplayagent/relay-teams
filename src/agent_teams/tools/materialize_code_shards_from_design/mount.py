from __future__ import annotations

import json
from uuid import uuid4

from pydantic_ai import Agent

from agent_teams.core.models import TaskEnvelope, VerificationPlan
from agent_teams.tools.runtime import ToolDeps
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.workflow.runtime_graph import (
    design_doc_path,
    load_graph,
    module_tag,
    parse_module_plan,
    save_graph,
    stage_tag,
    workflow_tag,
)


def materialize_code_shards_from_design_impl(
    deps: ToolDeps,
    *,
    root_task_id: str,
    workflow_id: str,
    parallel_limit: int = 4,
) -> dict[str, object]:
    graph = load_graph(deps.shared_store, task_id=root_task_id)
    if graph is None:
        raise KeyError('workflow_graph not found, call create_workflow_graph first')
    if graph.get('workflow_id') != workflow_id:
        raise ValueError(f'workflow_id mismatch: expected {graph.get("workflow_id")}, got {workflow_id}')

    if bool(graph.get('code_materialized')):
        return {
            'ok': True,
            'mode': graph.get('code_mode', 'parallel'),
            'workflow_id': workflow_id,
            'code_tasks': graph.get('code_tasks', []),
            'parallel_limit': int(graph.get('parallel_limit', 4)),
            'parse_error': None,
        }

    stages = graph.get('stages', {})
    if not isinstance(stages, dict):
        raise ValueError('invalid workflow graph stages')
    design_stage = stages.get('design')
    if not isinstance(design_stage, dict):
        raise ValueError('design stage missing in workflow graph')
    design_task_id = str(design_stage.get('task_id', ''))
    if not design_task_id:
        raise ValueError('design stage task_id missing')

    task_records = {record.envelope.task_id: record for record in deps.task_repo.list_by_trace(deps.trace_id)}
    design_record = task_records.get(design_task_id)
    if design_record is None:
        raise KeyError(f'design task not found in current trace: {design_task_id}')
    if design_record.status.value != 'completed':
        raise ValueError('design stage must be completed before materializing code shards')

    path = design_doc_path(workspace_root=deps.workspace_root, run_id=deps.run_id)
    mode = 'parallel'
    parse_error: str | None = None
    modules_payload: list[tuple[str, tuple[str, ...], str]] = []
    if path.exists() and path.is_file():
        text = path.read_text(encoding='utf-8')
        try:
            modules = parse_module_plan(text)
            for item in modules:
                modules_payload.append((item.module_id, item.files, item.scope))
        except ValueError as exc:
            mode = 'fallback_single'
            parse_error = str(exc)
    else:
        mode = 'fallback_single'
        parse_error = f'design document not found: {path}'

    if not modules_payload:
        mode = 'fallback_single'
        modules_payload = [('fallback_all', ('*',), 'fallback single code task for full implementation scope')]

    bounded_parallel = max(1, min(int(parallel_limit), int(graph.get('parallel_limit', 4))))
    common_scope = (workflow_tag(workflow_id), stage_tag('code'))
    code_tasks: list[dict[str, object]] = []
    for module_id, files, scope_hint in modules_payload:
        task_id = f'task_{uuid4().hex[:12]}'
        objective = f'Implement module `{module_id}` for: {graph.get("objective", "")}'
        if module_id == 'fallback_all':
            objective = f'Implement full coding stage for: {graph.get("objective", "")}'
        parent_instruction = (
            f'Module: {module_id}\nFiles: {", ".join(files)}\nScope: {scope_hint or "N/A"}\n'
            'Follow design/spec documents. This is a demo task.'
        )
        envelope = TaskEnvelope(
            task_id=task_id,
            session_id=deps.session_id,
            trace_id=deps.trace_id,
            parent_task_id=design_task_id,
            objective=objective,
            parent_instruction=parent_instruction,
            scope=common_scope + (module_tag(module_id),),
            dod=('implementation_done', 'tests_updated', 'non_empty_response'),
            verification=VerificationPlan(checklist=('non_empty_response',)),
        )
        deps.task_repo.create(envelope)
        code_tasks.append({'task_id': task_id, 'module_id': module_id})

    verify_stage = stages.get('verify')
    if isinstance(verify_stage, dict):
        verify_stage['depends_on'] = [item['task_id'] for item in code_tasks]

    graph['parallel_limit'] = bounded_parallel
    graph['code_materialized'] = True
    graph['code_mode'] = mode
    graph['code_tasks'] = code_tasks
    graph['stages'] = stages
    save_graph(deps.shared_store, task_id=root_task_id, graph=graph)
    return {
        'ok': True,
        'mode': mode,
        'workflow_id': workflow_id,
        'parse_error': parse_error,
        'code_tasks': code_tasks,
        'parallel_limit': bounded_parallel,
    }


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def materialize_code_shards_from_design(ctx, workflow_id: str, parallel_limit: int = 4) -> str:
        def _action() -> str:
            payload = materialize_code_shards_from_design_impl(
                ctx.deps,
                root_task_id=ctx.deps.task_id,
                workflow_id=workflow_id,
                parallel_limit=parallel_limit,
            )
            return json.dumps(payload, ensure_ascii=False)

        return execute_tool(
            ctx,
            tool_name='materialize_code_shards_from_design',
            args_summary={'workflow_id': workflow_id, 'parallel_limit': parallel_limit},
            action=_action,
        )
