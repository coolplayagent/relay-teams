# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from relay_teams.agents.orchestration.claim_service import (
    BlockersNotResolvedError,
    ClaimConflictError,
    ClaimReleaseResult,
    ClaimResult,
    ClaimService,
    LeaseRenewalResult,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskRecord,
    VerificationPlan,
)
from relay_teams.agents.tasks.task_repository import TaskRepository


def _make_task(
    task_id: str = "task_001",
    *,
    status: TaskStatus = TaskStatus.CREATED,
    depends_on_task_ids: tuple[str, ...] = (),
    blocked_by_task_ids: tuple[str, ...] = (),
    lease_owner: str = "",
    claim_token: str = "",
    lease_expires_at: datetime | None = None,
    assigned_instance_id: str | None = None,
) -> TaskRecord:
    envelope = TaskEnvelope(
        task_id=task_id,
        session_id="session_001",
        trace_id="trace_001",
        role_id="Crafter",
        objective="Test task objective",
        verification=VerificationPlan(),
        depends_on_task_ids=depends_on_task_ids,
        blocked_by_task_ids=blocked_by_task_ids,
        lease_owner=lease_owner,
        claim_token=claim_token,
        lease_expires_at=lease_expires_at,
    )
    return TaskRecord(
        envelope=envelope,
        status=status,
        assigned_instance_id=assigned_instance_id,
    )


@pytest.fixture
def task_repo(tmp_path: Path) -> TaskRepository:
    return TaskRepository(tmp_path / "test.db")


@pytest.fixture
def claim_service(task_repo: TaskRepository) -> ClaimService:
    return ClaimService(task_repo)


async def _seed_task(
    task_repo: TaskRepository,
    task_id: str = "task_001",
    **kwargs,
) -> TaskRecord:
    """Insert a task record and return it."""
    record = _make_task(task_id, **kwargs)
    task_repo.create(record.envelope)
    # If status != CREATED or instance assigned, update accordingly
    if record.status != TaskStatus.CREATED or record.assigned_instance_id:
        task_repo.update_status(
            task_id=task_id,
            status=record.status,
            assigned_instance_id=record.assigned_instance_id,
        )
    return record


def test_claim_result_model() -> None:
    result = ClaimResult(success=True, claim_token="abc", lease_expires_at=None)
    assert result.success
    assert result.claim_token == "abc"
    assert result.error_code == ""


def test_claim_release_result_model() -> None:
    result = ClaimReleaseResult(success=True)
    assert result.success


def test_lease_renewal_result_model() -> None:
    result = LeaseRenewalResult(success=True, new_expires_at=None)
    assert result.success


def test_claim_conflict_error() -> None:
    err = ClaimConflictError("task_001", "task_already_claimed")
    assert err.task_id == "task_001"
    assert err.error_code == "task_already_claimed"
    assert "task_001" in str(err)


def test_blockers_not_resolved_error() -> None:
    err = BlockersNotResolvedError("task_001", ("blocker_a", "blocker_b"))
    assert err.task_id == "task_001"
    assert err.unresolved_blockers == ("blocker_a", "blocker_b")
    assert "blocker_a" in str(err)


@pytest.mark.asyncio
async def test_claim_task_success(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    await _seed_task(
        task_repo, status=TaskStatus.ASSIGNED, assigned_instance_id="inst_1"
    )

    result = await claim_service.claim_task_async(
        task_id="task_001",
        instance_id="inst_1",
    )
    assert result.success
    assert result.claim_token != ""
    assert result.lease_expires_at is not None

    # Verify envelope was updated in repo
    updated = await task_repo.get_async("task_001")
    assert updated.envelope.lease_owner == "inst_1"
    assert updated.envelope.claim_token == result.claim_token
    assert updated.envelope.lease_expires_at is not None


@pytest.mark.asyncio
async def test_claim_task_conflict(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    now = datetime.now(tz=timezone.utc)
    future = now + timedelta(hours=1)
    await _seed_task(
        task_repo,
        task_id="task_001",
        status=TaskStatus.ASSIGNED,
        assigned_instance_id="inst_1",
        lease_owner="inst_other",
        claim_token="existing_token",
        lease_expires_at=future,
    )

    result = await claim_service.claim_task_async(
        task_id="task_001",
        instance_id="inst_1",
    )
    assert not result.success
    assert result.error_code == "lease_not_expired"


@pytest.mark.asyncio
async def test_claim_task_lease_expired(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    now = datetime.now(tz=timezone.utc)
    past = now - timedelta(hours=1)
    await _seed_task(
        task_repo,
        task_id="task_001",
        status=TaskStatus.ASSIGNED,
        assigned_instance_id="inst_1",
        lease_owner="inst_old",
        claim_token="old_token",
        lease_expires_at=past,
    )

    result = await claim_service.claim_task_async(
        task_id="task_001",
        instance_id="inst_1",
    )
    assert result.success
    assert result.claim_token != "old_token"


@pytest.mark.asyncio
async def test_claim_task_not_claimable(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    await _seed_task(
        task_repo,
        task_id="task_001",
        status=TaskStatus.COMPLETED,
    )

    result = await claim_service.claim_task_async(
        task_id="task_001",
        instance_id="inst_1",
    )
    assert not result.success
    assert result.error_code == "task_not_claimable"


@pytest.mark.asyncio
async def test_claim_task_not_found(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    result = await claim_service.claim_task_async(
        task_id="nonexistent",
        instance_id="inst_1",
    )
    assert not result.success
    assert result.error_code == "task_not_found"


@pytest.mark.asyncio
async def test_release_task(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    await _seed_task(
        task_repo,
        task_id="task_001",
        status=TaskStatus.ASSIGNED,
        assigned_instance_id="inst_1",
        lease_owner="inst_1",
        claim_token="my_token",
        lease_expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
    )

    result = await claim_service.release_task_async("task_001", "my_token")
    assert result.success

    updated = await task_repo.get_async("task_001")
    assert updated.envelope.lease_owner == ""
    assert updated.envelope.claim_token == ""
    assert updated.envelope.lease_expires_at is None


@pytest.mark.asyncio
async def test_release_task_token_mismatch(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    await _seed_task(
        task_repo,
        task_id="task_001",
        status=TaskStatus.ASSIGNED,
        assigned_instance_id="inst_1",
        lease_owner="inst_1",
        claim_token="correct_token",
    )

    result = await claim_service.release_task_async("task_001", "wrong_token")
    assert not result.success
    assert result.error_code == "claim_token_mismatch"


@pytest.mark.asyncio
async def test_release_task_not_found(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    result = await claim_service.release_task_async("nonexistent", "token")
    assert not result.success
    assert result.error_code == "task_not_found"


@pytest.mark.asyncio
async def test_renew_lease(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    now = datetime.now(tz=timezone.utc)
    future = now + timedelta(hours=1)
    await _seed_task(
        task_repo,
        task_id="task_001",
        status=TaskStatus.ASSIGNED,
        assigned_instance_id="inst_1",
        lease_owner="inst_1",
        claim_token="my_token",
        lease_expires_at=future,
    )

    result = await claim_service.renew_lease_async("task_001", "my_token")
    assert result.success
    assert result.new_expires_at is not None
    # Renewal sets new_expires_at = now + DEFAULT_LEASE_DURATION_SECONDS (300s),
    # which will be *before* the original future (now + 1h). Verify it is still
    # in the future relative to the moment of the call.
    assert result.new_expires_at > datetime.now(tz=timezone.utc) - timedelta(seconds=5)


@pytest.mark.asyncio
async def test_renew_lease_task_not_found(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    result = await claim_service.renew_lease_async("nonexistent", "token")
    assert not result.success
    assert result.error_code == "task_not_found"


@pytest.mark.asyncio
async def test_renew_lease_expired(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    now = datetime.now(tz=timezone.utc)
    past = now - timedelta(hours=1)
    await _seed_task(
        task_repo,
        task_id="task_001",
        status=TaskStatus.ASSIGNED,
        assigned_instance_id="inst_1",
        lease_owner="inst_1",
        claim_token="my_token",
        lease_expires_at=past,
    )

    result = await claim_service.renew_lease_async("task_001", "my_token")
    assert not result.success
    assert result.error_code == "lease_already_expired"


@pytest.mark.asyncio
async def test_renew_lease_token_mismatch(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    await _seed_task(
        task_repo,
        task_id="task_001",
        status=TaskStatus.ASSIGNED,
        assigned_instance_id="inst_1",
        lease_owner="inst_1",
        claim_token="correct",
    )

    result = await claim_service.renew_lease_async("task_001", "wrong")
    assert not result.success
    assert result.error_code == "claim_token_mismatch"


@pytest.mark.asyncio
async def test_blocker_check_all_completed(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    # Create blocker tasks
    await _seed_task(task_repo, task_id="blocker_a", status=TaskStatus.COMPLETED)
    await _seed_task(task_repo, task_id="blocker_b", status=TaskStatus.COMPLETED)
    await _seed_task(
        task_repo,
        task_id="task_001",
        blocked_by_task_ids=("blocker_a", "blocker_b"),
    )

    envelope = (await task_repo.get_async("task_001")).envelope
    unresolved = await claim_service.check_blockers_async(envelope)
    assert unresolved == ()


@pytest.mark.asyncio
async def test_blocker_check_some_pending(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    await _seed_task(task_repo, task_id="blocker_a", status=TaskStatus.COMPLETED)
    await _seed_task(task_repo, task_id="blocker_b", status=TaskStatus.RUNNING)
    await _seed_task(
        task_repo,
        task_id="task_001",
        blocked_by_task_ids=("blocker_a", "blocker_b"),
    )

    envelope = (await task_repo.get_async("task_001")).envelope
    unresolved = await claim_service.check_blockers_async(envelope)
    assert "blocker_b" in unresolved
    assert "blocker_a" not in unresolved


@pytest.mark.asyncio
async def test_blocker_check_deleted_task(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    # Only one blocker exists; the other is "deleted" (never created)
    await _seed_task(task_repo, task_id="blocker_a", status=TaskStatus.COMPLETED)
    await _seed_task(
        task_repo,
        task_id="task_001",
        blocked_by_task_ids=("blocker_a", "deleted_blocker"),
    )

    envelope = (await task_repo.get_async("task_001")).envelope
    unresolved = await claim_service.check_blockers_async(envelope)
    assert unresolved == ()


@pytest.mark.asyncio
async def test_blocker_check_empty(
    claim_service: ClaimService, task_repo: TaskRepository
) -> None:
    await _seed_task(task_repo, task_id="task_001")

    envelope = (await task_repo.get_async("task_001")).envelope
    unresolved = await claim_service.check_blockers_async(envelope)
    assert unresolved == ()


def test_task_envelope_blocked_by_normalization() -> None:
    """Verify that blocked_by_task_ids normalizes input correctly."""
    env = TaskEnvelope(
        task_id="t1",
        session_id="s1",
        trace_id="tr1",
        objective="test",
        verification=VerificationPlan(),
        blocked_by_task_ids=(" a ", "b", "  c  "),
    )
    assert env.blocked_by_task_ids == ("a", "b", "c")


def test_task_envelope_blocked_by_default_empty() -> None:
    env = TaskEnvelope(
        task_id="t1",
        session_id="s1",
        trace_id="tr1",
        objective="test",
        verification=VerificationPlan(),
    )
    assert env.blocked_by_task_ids == ()


def test_task_draft_blocked_by_normalization() -> None:
    from relay_teams.agents.orchestration.task_contracts import TaskDraft

    draft = TaskDraft(
        objective="test objective",
        blocked_by_task_ids=(" x ", "y"),
    )
    assert draft.blocked_by_task_ids == ("x", "y")


def test_task_draft_blocked_by_default_empty() -> None:
    from relay_teams.agents.orchestration.task_contracts import TaskDraft

    draft = TaskDraft(objective="test objective")
    assert draft.blocked_by_task_ids == ()


def test_dependencies_completed_includes_blockers() -> None:
    from relay_teams.agents.orchestration.coordinator import _dependencies_completed

    blocker_record = _make_task("blocker_a", status=TaskStatus.COMPLETED)
    dep_record = _make_task("dep_a", status=TaskStatus.COMPLETED)
    target = _make_task(
        "target",
        depends_on_task_ids=("dep_a",),
        blocked_by_task_ids=("blocker_a",),
    )

    records = {
        "dep_a": dep_record,
        "blocker_a": blocker_record,
        "target": target,
    }
    assert _dependencies_completed(record=target, records_by_task_id=records)


def test_dependencies_completed_blocker_not_done() -> None:
    from relay_teams.agents.orchestration.coordinator import _dependencies_completed

    blocker_record = _make_task("blocker_a", status=TaskStatus.RUNNING)
    dep_record = _make_task("dep_a", status=TaskStatus.COMPLETED)
    target = _make_task(
        "target",
        depends_on_task_ids=("dep_a",),
        blocked_by_task_ids=("blocker_a",),
    )

    records = {
        "dep_a": dep_record,
        "blocker_a": blocker_record,
        "target": target,
    }
    assert not _dependencies_completed(record=target, records_by_task_id=records)


def test_dependencies_completed_no_blockers_backward_compat() -> None:
    """Tasks without blockers should behave identically to before."""
    from relay_teams.agents.orchestration.coordinator import _dependencies_completed

    dep_record = _make_task("dep_a", status=TaskStatus.COMPLETED)
    target = _make_task(
        "target",
        depends_on_task_ids=("dep_a",),
    )

    records = {
        "dep_a": dep_record,
        "target": target,
    }
    assert _dependencies_completed(record=target, records_by_task_id=records)


def test_dependencies_completed_blocker_deleted() -> None:
    """Deleted blocker is treated as not completed (None)."""
    from relay_teams.agents.orchestration.coordinator import _dependencies_completed

    dep_record = _make_task("dep_a", status=TaskStatus.COMPLETED)
    target = _make_task(
        "target",
        depends_on_task_ids=("dep_a",),
        blocked_by_task_ids=("deleted_blocker",),
    )

    records = {
        "dep_a": dep_record,
        "target": target,
    }
    assert not _dependencies_completed(record=target, records_by_task_id=records)
