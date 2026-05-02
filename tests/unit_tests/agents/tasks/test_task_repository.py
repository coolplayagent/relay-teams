from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, TaskSpec, VerificationPlan


def _create_task(
    repo: TaskRepository,
    task_id: str = "task-1",
    *,
    session_id: str = "session-1",
    trace_id: str = "run-1",
) -> None:
    _ = repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id=session_id,
            parent_task_id=None,
            trace_id=trace_id,
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


@pytest.mark.asyncio
async def test_async_task_repository_methods_share_persisted_state(
    tmp_path: Path,
) -> None:
    repo = TaskRepository(tmp_path / "task_repo_async.db")
    envelope = TaskEnvelope(
        task_id="task-async",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        objective="demo",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )

    try:
        created = await repo.create_async(envelope)
        await repo.update_status_async(
            "task-async",
            TaskStatus.COMPLETED,
            assigned_instance_id="inst-1",
            result="done",
        )
        by_trace = await repo.list_by_trace_async("run-1")
        by_session = await repo.list_by_session_async("session-1")
        fetched = await repo.get_async("task-async")
    finally:
        await repo.close_async()

    assert created.envelope.task_id == "task-async"
    assert tuple(record.envelope.task_id for record in by_trace) == ("task-async",)
    assert tuple(record.envelope.task_id for record in by_session) == ("task-async",)
    assert fetched.status == TaskStatus.COMPLETED
    assert fetched.assigned_instance_id == "inst-1"
    assert fetched.result == "done"


@pytest.mark.asyncio
async def test_task_repository_lists_session_run_ids(tmp_path: Path) -> None:
    repo = TaskRepository(tmp_path / "task_repo_session_run_ids.db")
    _create_task(repo, "task-run-2", trace_id="run-2")
    _create_task(repo, "task-run-1", trace_id="run-1")
    _create_task(repo, "task-other-session", session_id="session-2", trace_id="run-1")

    try:
        empty_records = repo.list_by_session_run_ids("session-1", ("", "   "))
        records = await repo.list_by_session_run_ids_async(
            "session-1",
            ("run-1", "run-2", "run-1"),
        )
    finally:
        await repo.close_async()

    assert empty_records == ()
    assert tuple(record.envelope.task_id for record in records) == (
        "task-run-2",
        "task-run-1",
    )


@pytest.mark.asyncio
async def test_heartbeat_running_async_preserves_terminal_state(
    tmp_path: Path,
) -> None:
    repo = TaskRepository(tmp_path / "task_repo_heartbeat.db")
    envelope = TaskEnvelope(
        task_id="task-heartbeat",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        objective="demo",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )

    try:
        await repo.create_async(envelope)
        await repo.update_status_async(
            "task-heartbeat",
            TaskStatus.RUNNING,
            assigned_instance_id="inst-1",
        )
        stale_updated = await repo.heartbeat_running_async(
            "task-heartbeat",
            assigned_instance_id="inst-2",
        )
        running_updated = await repo.heartbeat_running_async(
            "task-heartbeat",
            assigned_instance_id="inst-1",
        )
        running = await repo.get_async("task-heartbeat")
        await repo.update_status_async(
            "task-heartbeat",
            TaskStatus.COMPLETED,
            assigned_instance_id="inst-1",
            result="done",
        )
        updated = await repo.heartbeat_running_async(
            "task-heartbeat",
            assigned_instance_id="inst-1",
        )
        fetched = await repo.get_async("task-heartbeat")
    finally:
        await repo.close_async()

    assert stale_updated is False
    assert running_updated is True
    assert running.status == TaskStatus.RUNNING
    assert running.assigned_instance_id == "inst-1"
    assert updated is False
    assert fetched.status == TaskStatus.COMPLETED
    assert fetched.assigned_instance_id == "inst-1"
    assert fetched.result == "done"


@pytest.mark.asyncio
async def test_task_repository_async_hot_paths_do_not_reinitialize_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = TaskRepository(tmp_path / "task_repo_async_no_reinit.db")
    envelope = TaskEnvelope(
        task_id="task-async",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        objective="demo",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    updated_envelope = envelope.model_copy(update={"objective": "updated"})
    delete_envelope = TaskEnvelope(
        task_id="task-delete",
        session_id="session-delete",
        parent_task_id=None,
        trace_id="run-delete",
        objective="delete",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )

    async def _fail_init() -> None:
        raise AssertionError("async schema init should not run on hot paths")

    try:
        await repo._init_tables_async()
        monkeypatch.setattr(repo, "_init_tables_async", _fail_init)
        created = await repo.create_async(envelope)
        updated = await repo.update_envelope_async("task-async", updated_envelope)
        await repo.update_status_async(
            "task-async",
            TaskStatus.COMPLETED,
            assigned_instance_id="inst-1",
            result="done",
        )
        fetched = await repo.get_async("task-async")
        all_records = await repo.list_all_async()
        by_trace = await repo.list_by_trace_async("run-1")
        by_session = await repo.list_by_session_async("session-1")
        await repo.create_async(delete_envelope)
        await repo.delete_by_session_async("session-delete")
        await repo.delete_async("task-async")
    finally:
        await repo.close_async()

    assert created.envelope.task_id == "task-async"
    assert updated.envelope.objective == "updated"
    assert fetched.status == TaskStatus.COMPLETED
    assert tuple(record.envelope.task_id for record in all_records) == ("task-async",)
    assert tuple(record.envelope.task_id for record in by_trace) == ("task-async",)
    assert tuple(record.envelope.task_id for record in by_session) == ("task-async",)


def test_create_persists_spec_artifact_and_binds_envelope(tmp_path: Path) -> None:
    repo = TaskRepository(tmp_path / "task_repo_spec_artifact.db")
    envelope = TaskEnvelope(
        task_id="task-spec",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="implement spec",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        spec=TaskSpec(summary="Persist this spec"),
        spec_source_task_id="task-designer",
    )

    created = repo.create(envelope)
    artifact = repo.get_latest_spec_artifact_for_task("task-spec")
    fetched = repo.get("task-spec")

    assert created.envelope.spec_artifact_id is not None
    assert fetched.envelope.spec_artifact_id == artifact.artifact_id
    assert artifact.spec.summary == "Persist this spec"
    assert artifact.source_task_id == "task-designer"
    assert artifact.version == 1


def test_update_envelope_versions_changed_spec_artifact(tmp_path: Path) -> None:
    repo = TaskRepository(tmp_path / "task_repo_spec_versions.db")
    created = repo.create(
        TaskEnvelope(
            task_id="task-spec",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            objective="implement spec",
            verification=VerificationPlan(checklist=("non_empty_response",)),
            spec=TaskSpec(summary="v1"),
        )
    )

    updated = repo.update_envelope(
        "task-spec",
        created.envelope.model_copy(update={"spec": TaskSpec(summary="v2")}),
    )
    artifacts = repo.list_spec_artifacts_by_task("task-spec")

    assert tuple(artifact.version for artifact in artifacts) == (1, 2)
    assert artifacts[0].artifact_id != artifacts[1].artifact_id
    assert updated.envelope.spec_artifact_id == artifacts[1].artifact_id
    assert updated.envelope.spec is not None
    assert updated.envelope.spec.prompt_artifact_version == 2


def test_update_envelope_rejects_cross_task_spec_artifact_rebinding(
    tmp_path: Path,
) -> None:
    repo = TaskRepository(tmp_path / "task_repo_cross_spec_artifact.db")
    source = repo.create(
        TaskEnvelope(
            task_id="task-source",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            objective="source spec",
            verification=VerificationPlan(checklist=("non_empty_response",)),
            spec=TaskSpec(summary="Shared spec"),
        )
    )
    target = repo.create(
        TaskEnvelope(
            task_id="task-target",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            objective="target spec",
            verification=VerificationPlan(checklist=("non_empty_response",)),
            spec=TaskSpec(summary="Shared spec"),
        )
    )

    with pytest.raises(
        ValueError, match="spec_artifact_id references a different task"
    ):
        repo.update_envelope(
            "task-target",
            target.envelope.model_copy(
                update={"spec_artifact_id": source.envelope.spec_artifact_id}
            ),
        )

    fetched = repo.get("task-target")
    assert fetched.envelope.spec_artifact_id == target.envelope.spec_artifact_id


def test_spec_artifact_versions_follow_local_task_history(tmp_path: Path) -> None:
    repo = TaskRepository(tmp_path / "task_repo_spec_local_versions.db")
    created = repo.create(
        TaskEnvelope(
            task_id="task-spec",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            objective="import spec",
            verification=VerificationPlan(checklist=("non_empty_response",)),
            spec=TaskSpec(summary="Imported", prompt_artifact_version=99),
        )
    )

    updated = repo.update_envelope(
        "task-spec",
        created.envelope.model_copy(
            update={"spec": TaskSpec(summary="Updated", prompt_artifact_version=50)}
        ),
    )
    artifacts = repo.list_spec_artifacts_by_task("task-spec")

    assert tuple(artifact.version for artifact in artifacts) == (1, 2)
    assert tuple(artifact.spec.prompt_artifact_version for artifact in artifacts) == (
        1,
        2,
    )
    assert updated.envelope.spec is not None
    assert updated.envelope.spec.prompt_artifact_version == 2


def test_spec_artifact_accessors_and_reuse_edges(tmp_path: Path) -> None:
    repo = TaskRepository(tmp_path / "task_repo_spec_accessors.db")
    created = repo.create(
        TaskEnvelope(
            task_id="task-spec",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            objective="persist spec",
            verification=VerificationPlan(checklist=("non_empty_response",)),
            spec=TaskSpec(summary="v1"),
        )
    )
    artifact_id = created.envelope.spec_artifact_id
    assert artifact_id is not None

    reused = repo.update_envelope(
        "task-spec",
        created.envelope.model_copy(update={"spec_source_task_id": "task-source"}),
    )
    v2 = repo.update_envelope(
        "task-spec",
        reused.envelope.model_copy(update={"spec": TaskSpec(summary="v2")}),
    )
    v1_artifact = repo.get_spec_artifact(artifact_id)
    with pytest.raises(ValueError, match="different task spec"):
        repo.update_envelope(
            "task-spec",
            v2.envelope.model_copy(
                update={
                    "spec": TaskSpec(summary="different"),
                    "spec_artifact_id": artifact_id,
                }
            ),
        )
    rolled_back = repo.update_envelope(
        "task-spec",
        v2.envelope.model_copy(
            update={"spec": v1_artifact.spec, "spec_artifact_id": artifact_id}
        ),
    )

    assert reused.envelope.spec_artifact_id == artifact_id
    assert reused.envelope.spec_source_task_id == "task-source"
    assert repo.get_spec_artifact(artifact_id).spec.summary == "v1"
    assert repo.get_latest_spec_artifact_for_task("task-spec").version == 2
    assert len(repo.list_spec_artifacts_by_task("task-spec")) == 2
    assert rolled_back.envelope.spec_artifact_id == artifact_id
    with pytest.raises(KeyError, match="Unknown spec artifact_id"):
        repo.get_spec_artifact("missing-spec")
    with pytest.raises(KeyError, match="No spec artifact found"):
        repo.get_latest_spec_artifact_for_task("missing-task")


@pytest.mark.asyncio
async def test_async_spec_artifact_accessors_and_reuse_edges(tmp_path: Path) -> None:
    repo = TaskRepository(tmp_path / "task_repo_async_spec_accessors.db")
    envelope = TaskEnvelope(
        task_id="task-spec",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        objective="persist spec",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        spec=TaskSpec(summary="v1"),
    )

    try:
        created = await repo.create_async(envelope)
        artifact_id = created.envelope.spec_artifact_id
        assert artifact_id is not None
        reused = await repo.update_envelope_async(
            "task-spec",
            created.envelope.model_copy(update={"spec_source_task_id": "task-source"}),
        )
        fetched = await repo.get_spec_artifact_async(artifact_id)
        latest = await repo.get_latest_spec_artifact_for_task_async("task-spec")
        artifacts = await repo.list_spec_artifacts_by_task_async("task-spec")
        with pytest.raises(KeyError, match="Unknown spec artifact_id"):
            await repo.get_spec_artifact_async("missing-spec")
        with pytest.raises(KeyError, match="No spec artifact found"):
            await repo.get_latest_spec_artifact_for_task_async("missing-task")
    finally:
        await repo.close_async()

    assert reused.envelope.spec_artifact_id == artifact_id
    assert reused.envelope.spec_source_task_id == "task-source"
    assert fetched.spec.summary == "v1"
    assert latest.artifact_id == artifact_id
    assert tuple(artifact.artifact_id for artifact in artifacts) == (artifact_id,)
