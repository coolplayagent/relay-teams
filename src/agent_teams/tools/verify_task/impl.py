from __future__ import annotations

from agent_teams.core.enums import EventType, TaskStatus
from agent_teams.core.models import EventEnvelope, VerificationResult
from agent_teams.events.event_bus import EventBus
from agent_teams.state.task_repo import TaskRepository


def verify_task(task_repo: TaskRepository, event_bus: EventBus, task_id: str) -> VerificationResult:
    task = task_repo.get(task_id)
    if task.status != TaskStatus.COMPLETED or task.result is None:
        passed = False
        details = ('Task not completed yet',)
        event_type = EventType.VERIFICATION_FAILED
    else:
        checklist = task.envelope.verification.checklist
        result = task.result.lower()
        missing_items: list[str] = []
        for item in checklist:
            key = item.lower()
            if key == 'non_empty_response':
                if not task.result.strip():
                    missing_items.append(item)
                continue
            if key not in result:
                missing_items.append(item)
        missing = tuple(missing_items)
        passed = len(missing) == 0
        details = ('All checklist items found in result',) if passed else missing
        event_type = EventType.VERIFICATION_PASSED if passed else EventType.VERIFICATION_FAILED

    verification = VerificationResult(task_id=task.envelope.task_id, passed=passed, details=details)
    event_bus.emit(
        EventEnvelope(
            event_type=event_type,
            trace_id=task.envelope.trace_id,
            session_id=task.envelope.session_id,
            task_id=task.envelope.task_id,
            payload_json=verification.model_dump_json(),
        )
    )
    return verification
