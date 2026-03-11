# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.reflection.models import (
    MemoryOwnerScope,
    ReflectionJobCreate,
    ReflectionJobStatus,
    ReflectionJobType,
)
from agent_teams.reflection.repository import ReflectionJobRepository


def test_repository_enqueue_claim_retry_and_reset(tmp_path: Path) -> None:
    repo = ReflectionJobRepository(tmp_path / "reflection_jobs.db")
    created = repo.enqueue(
        ReflectionJobCreate(
            job_type=ReflectionJobType.DAILY_REFLECTION,
            session_id="session-1",
            run_id="run-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="writer_agent",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            memory_owner_scope=MemoryOwnerScope.SESSION_ROLE,
            memory_owner_id="session-1:writer_agent",
            trigger_date="2026-03-11",
        )
    )

    claimed = repo.claim_next_job(max_retry_attempts=3)

    assert claimed is not None
    assert claimed.job_id == created.job_id
    assert claimed.status == ReflectionJobStatus.RUNNING
    assert claimed.attempt_count == 1

    repo.mark_failed(claimed.job_id, last_error="boom")
    failed = repo.get(claimed.job_id)
    assert failed.status == ReflectionJobStatus.FAILED
    assert failed.last_error == "boom"

    retried = repo.retry(claimed.job_id)
    assert retried.status == ReflectionJobStatus.QUEUED
    assert retried.last_error is None

    running_again = repo.claim_next_job(max_retry_attempts=3)
    assert running_again is not None
    assert running_again.job_id == created.job_id
    assert running_again.attempt_count == 2

    repo.reset_running_to_queued()
    queued_again = repo.get(created.job_id)
    assert queued_again.status == ReflectionJobStatus.QUEUED


def test_repository_detects_existing_owner_job_for_date(tmp_path: Path) -> None:
    repo = ReflectionJobRepository(tmp_path / "reflection_jobs_owner.db")
    _ = repo.enqueue(
        ReflectionJobCreate(
            job_type=ReflectionJobType.LONG_TERM_CONSOLIDATION,
            session_id="session-1",
            run_id="run-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="writer_agent",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            memory_owner_scope=MemoryOwnerScope.SESSION_ROLE,
            memory_owner_id="session-1:writer_agent",
            trigger_date="2026-03-11",
        )
    )

    assert repo.has_job_for_owner_date(
        job_type=ReflectionJobType.LONG_TERM_CONSOLIDATION,
        memory_owner_id="session-1:writer_agent",
        trigger_date="2026-03-11",
    )
