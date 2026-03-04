from agent_teams.core.enums import TaskStatus
from agent_teams.core.models import TaskEnvelope, TaskRecord, VerificationPlan
from agent_teams.tools.workflow.dispatch_tasks import (
    _converged_stage,
    _latest_completed_task,
    _next_action,
    _progress,
)


def _record(task_id: str, status: TaskStatus) -> TaskRecord:
    envelope = TaskEnvelope(
        task_id=task_id,
        session_id='session-1',
        parent_task_id='root-task',
        trace_id='run-1',
        objective='demo',
        verification=VerificationPlan(checklist=('non_empty_response',)),
    )
    return TaskRecord(
        envelope=envelope,
        status=status,
        assigned_instance_id='inst-1',
    )


def test_latest_completed_task_prefers_most_recent_stage_order() -> None:
    tasks = {
        'spec': {'task_id': 'task-spec'},
        'design': {'task_id': 'task-design'},
        'code': {'task_id': 'task-code'},
    }
    records = {
        'task-spec': _record('task-spec', TaskStatus.COMPLETED),
        'task-design': _record('task-design', TaskStatus.COMPLETED),
        'task-code': _record('task-code', TaskStatus.CREATED),
    }
    latest = _latest_completed_task(tasks=tasks, records=records)
    assert latest == ('design', 'task-design')


def test_converged_stage_and_next_action() -> None:
    tasks = {
        'spec': {'task_id': 'task-spec'},
        'design': {'task_id': 'task-design'},
    }
    records = {
        'task-spec': _record('task-spec', TaskStatus.COMPLETED),
        'task-design': _record('task-design', TaskStatus.CREATED),
    }
    progress = _progress(tasks=tasks, records=records)
    assert progress == {'completed': 1, 'total': 2}

    stage = _converged_stage(progress=progress, failed=[])
    assert stage == 'progress_1_2'
    assert _next_action(stage, failed=[]) == 'next'

    failed: list[dict[str, str]] = [{'task_id': 'task-design'}]
    failed_stage = _converged_stage(progress=progress, failed=failed)
    assert failed_stage == 'failed'
    assert _next_action(failed_stage, failed=failed) == 'revise'
