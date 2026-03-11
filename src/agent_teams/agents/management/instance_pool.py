# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.ids import new_instance_id
from agent_teams.agents.models import SubAgentInstance
from agent_teams.workspace import (
    build_conversation_id,
    build_instance_conversation_id,
    build_instance_workspace_id,
    build_workspace_id,
)


class InstancePool:
    def __init__(self) -> None:
        self._instances: list[SubAgentInstance] = []

    @classmethod
    def from_repo(cls, repo: object) -> "InstancePool":
        """Rebuild pool from DB on startup, marking any stale RUNNING instances as FAILED."""
        from agent_teams.state.agent_repo import AgentInstanceRepository

        assert isinstance(repo, AgentInstanceRepository)
        pool = cls()
        for record in repo.list_all():
            status = record.status
            # Any instance left RUNNING in the DB means the process died mid-task; mark FAILED.
            if status == InstanceStatus.RUNNING:
                status = InstanceStatus.FAILED
                repo.mark_status(record.instance_id, InstanceStatus.FAILED)
            instance = SubAgentInstance(
                instance_id=record.instance_id,
                role_id=record.role_id,
                workspace_id=record.workspace_id
                or build_workspace_id(record.session_id),
                conversation_id=record.conversation_id
                or build_conversation_id(record.session_id, record.role_id),
                status=status,
                created_at=record.created_at,
                last_active_at=record.updated_at,
            )
            pool._instances.append(instance)
        return pool

    def create_subagent(
        self,
        role_id: str,
        session_id: str | None = None,
        workspace_id: str | None = None,
        conversation_id: str | None = None,
    ) -> SubAgentInstance:
        instance_id = new_instance_id().value
        resolved_workspace_id = workspace_id
        resolved_conversation_id = conversation_id
        if session_id is not None:
            if resolved_workspace_id is None:
                resolved_workspace_id = build_instance_workspace_id(
                    session_id,
                    role_id,
                    instance_id,
                )
            if resolved_conversation_id is None:
                resolved_conversation_id = build_instance_conversation_id(
                    session_id,
                    role_id,
                    instance_id,
                )
        instance = SubAgentInstance(
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=resolved_workspace_id or instance_id,
            conversation_id=resolved_conversation_id or instance_id,
        )
        self._instances.append(instance)
        return instance

    def mark_running(self, instance_id: str) -> SubAgentInstance:
        return self._replace_status(instance_id, InstanceStatus.RUNNING)

    def mark_idle(self, instance_id: str) -> SubAgentInstance:
        return self._replace_status(instance_id, InstanceStatus.IDLE)

    def mark_completed(self, instance_id: str) -> SubAgentInstance:
        return self._replace_status(
            instance_id, InstanceStatus.COMPLETED, inc_completed=1
        )

    def mark_stopped(self, instance_id: str) -> SubAgentInstance:
        return self._replace_status(instance_id, InstanceStatus.STOPPED)

    def mark_failed(self, instance_id: str) -> SubAgentInstance:
        return self._replace_status(instance_id, InstanceStatus.FAILED, inc_failed=1)

    def mark_timeout(self, instance_id: str) -> SubAgentInstance:
        return self._replace_status(instance_id, InstanceStatus.TIMEOUT)

    def list_instances(self) -> tuple[SubAgentInstance, ...]:
        return tuple(self._instances)

    def get(self, instance_id: str) -> SubAgentInstance:
        for instance in self._instances:
            if instance.instance_id == instance_id:
                return instance
        raise KeyError(f"Unknown instance_id: {instance_id}")

    def cleanup_idle(self, idle_ttl_seconds: int) -> tuple[str, ...]:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=idle_ttl_seconds)
        removed_ids: list[str] = []
        kept: list[SubAgentInstance] = []
        for instance in self._instances:
            if (
                instance.status == InstanceStatus.IDLE
                and instance.last_active_at < cutoff
            ):
                removed_ids.append(instance.instance_id)
                continue
            kept.append(instance)
        self._instances = kept
        return tuple(removed_ids)

    def _replace_status(
        self,
        instance_id: str,
        status: InstanceStatus,
        inc_completed: int = 0,
        inc_failed: int = 0,
    ) -> SubAgentInstance:
        for idx, instance in enumerate(self._instances):
            if instance.instance_id != instance_id:
                continue
            updated = instance.model_copy(
                update={
                    "status": status,
                    "last_active_at": datetime.now(tz=timezone.utc),
                    "completed_tasks": instance.completed_tasks + inc_completed,
                    "failed_tasks": instance.failed_tasks + inc_failed,
                }
            )
            self._instances[idx] = updated
            return updated
        raise KeyError(f"Unknown instance_id: {instance_id}")
