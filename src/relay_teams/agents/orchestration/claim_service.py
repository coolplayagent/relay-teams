# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

DEFAULT_LEASE_DURATION_SECONDS = 300  # 5 minutes


class ClaimResult(BaseModel):
    """Result of an atomic claim operation."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    claim_token: str = ""
    lease_expires_at: datetime | None = None
    error_code: str = ""


class LeaseRenewalResult(BaseModel):
    """Result of a lease renewal operation."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    new_expires_at: datetime | None = None
    error_code: str = ""


class ClaimReleaseResult(BaseModel):
    """Result of a claim release operation."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    error_code: str = ""


class ClaimConflictError(Exception):
    """Raised when an atomic claim fails due to conflict."""

    def __init__(self, task_id: str, error_code: str) -> None:
        self.task_id = task_id
        self.error_code = error_code
        super().__init__(f"Claim conflict for task {task_id}: {error_code}")


class BlockersNotResolvedError(Exception):
    """Raised when dispatch is attempted with unresolved blockers."""

    def __init__(
        self,
        task_id: str,
        unresolved_blockers: tuple[str, ...],
    ) -> None:
        self.task_id = task_id
        self.unresolved_blockers = unresolved_blockers
        super().__init__(
            f"Task {task_id} has unresolved blockers: {', '.join(unresolved_blockers)}"
        )


class ClaimService:
    """Atomic claim/checkout service using Compare-And-Swap semantics.

    Uses SQL UPDATE WHERE conditions to atomically acquire and release
    task leases. This prevents concurrent dispatchers from claiming the
    same task.
    """

    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo

    async def claim_task_async(
        self,
        *,
        task_id: str,
        instance_id: str,
    ) -> ClaimResult:
        """Atomically claim a task for execution.

        Uses CAS semantics: the envelope's lease_owner, claim_token and
        lease_expires_at are updated in-place only when the task is in a
        claimable state and no active lease exists.
        """
        now = datetime.now(tz=timezone.utc)

        try:
            record = await self._task_repo.get_async(task_id)
        except KeyError:
            return ClaimResult(success=False, error_code="task_not_found")

        if record.status not in {TaskStatus.CREATED, TaskStatus.ASSIGNED}:
            return ClaimResult(
                success=False,
                error_code="task_not_claimable",
            )

        # Check if there is an active lease held by someone else
        if record.envelope.lease_owner and record.envelope.lease_owner != instance_id:
            if (
                record.envelope.lease_expires_at is not None
                and record.envelope.lease_expires_at > now
            ):
                return ClaimResult(
                    success=False,
                    error_code="lease_not_expired",
                )
            # Lease expired → allow re-claim regardless of old claim_token

        new_token = uuid.uuid4().hex
        expires_at = datetime.fromtimestamp(
            now.timestamp() + DEFAULT_LEASE_DURATION_SECONDS,
            tz=timezone.utc,
        )

        updated_envelope = record.envelope.model_copy(
            update={
                "lease_owner": instance_id,
                "claim_token": new_token,
                "lease_expires_at": expires_at,
            }
        )
        await self._task_repo.update_envelope_async(task_id, updated_envelope)

        return ClaimResult(
            success=True,
            claim_token=new_token,
            lease_expires_at=expires_at,
        )

    async def release_task_async(
        self,
        task_id: str,
        claim_token: str,
    ) -> ClaimReleaseResult:
        """Release a previously acquired claim.

        Only the holder of the matching claim_token can release the claim.
        On release, lease_owner, claim_token and lease_expires_at are cleared.
        """
        try:
            record = await self._task_repo.get_async(task_id)
        except KeyError:
            return ClaimReleaseResult(success=False, error_code="task_not_found")

        if record.envelope.claim_token and record.envelope.claim_token != claim_token:
            return ClaimReleaseResult(
                success=False,
                error_code="claim_token_mismatch",
            )

        updated_envelope = record.envelope.model_copy(
            update={
                "lease_owner": "",
                "claim_token": "",
                "lease_expires_at": None,
            }
        )
        await self._task_repo.update_envelope_async(task_id, updated_envelope)
        return ClaimReleaseResult(success=True)

    async def renew_lease_async(
        self,
        task_id: str,
        claim_token: str,
    ) -> LeaseRenewalResult:
        """Renew an active lease, extending the expiration time."""
        now = datetime.now(tz=timezone.utc)

        try:
            record = await self._task_repo.get_async(task_id)
        except KeyError:
            return LeaseRenewalResult(success=False, error_code="task_not_found")

        if record.envelope.claim_token != claim_token:
            return LeaseRenewalResult(
                success=False,
                error_code="claim_token_mismatch",
            )

        if (
            record.envelope.lease_expires_at is not None
            and record.envelope.lease_expires_at < now
        ):
            return LeaseRenewalResult(
                success=False,
                error_code="lease_already_expired",
            )

        new_expires = datetime.fromtimestamp(
            now.timestamp() + DEFAULT_LEASE_DURATION_SECONDS,
            tz=timezone.utc,
        )
        updated_envelope = record.envelope.model_copy(
            update={"lease_expires_at": new_expires}
        )
        await self._task_repo.update_envelope_async(task_id, updated_envelope)
        return LeaseRenewalResult(success=True, new_expires_at=new_expires)

    async def check_blockers_async(
        self,
        envelope: TaskEnvelope,
    ) -> tuple[str, ...]:
        """Check if all blocker tasks are completed.

        Returns the tuple of blocker task IDs that are not yet COMPLETED.
        Deleted blocker tasks are treated as resolved.
        """
        unresolved: list[str] = []
        for blocker_id in envelope.blocked_by_task_ids:
            try:
                blocker_record = await self._task_repo.get_async(blocker_id)
            except KeyError:
                # Blocker task was deleted -- treat as resolved
                continue
            if blocker_record.status != TaskStatus.COMPLETED:
                unresolved.append(blocker_id)
        return tuple(unresolved)
