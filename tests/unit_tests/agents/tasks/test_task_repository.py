from pathlib import Path

from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


def _create_task(repo: TaskRepository, task_id: str = "task-1") -> None:
    _ = repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="demo",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )


def test_update_status_clears_stale_error_on_retry(tmp_path: Path) -> None:
    repo = TaskRepository(tmp_path / "task_repo.db")
    _create_task(repo)

    repo.update_status(
        "task-1",
        TaskStatus.STOPPED,
        assigned_instance_id="inst-1",
        error_message="Task stopped by user",
    )
    repo.update_status(
        "task-1",
        TaskStatus.ASSIGNED,
        assigned_instance_id="inst-1",
    )

    record = repo.get("task-1")
    assert record.status == TaskStatus.ASSIGNED
    assert record.error_message is None


def test_update_status_clears_stale_result_when_task_restarts(tmp_path: Path) -> None:
    repo = TaskRepository(tmp_path / "task_repo_restart.db")
    _create_task(repo)

    repo.update_status("task-1", TaskStatus.COMPLETED, result="first result")
    repo.update_status(
        "task-1",
        TaskStatus.ASSIGNED,
        assigned_instance_id="inst-1",
    )

    assigned = repo.get("task-1")
    assert assigned.status == TaskStatus.ASSIGNED
    assert assigned.result is None

    repo.update_status("task-1", TaskStatus.COMPLETED, result="second result")
    completed = repo.get("task-1")
    assert completed.status == TaskStatus.COMPLETED
    assert completed.result == "second result"
    assert completed.error_message is None
