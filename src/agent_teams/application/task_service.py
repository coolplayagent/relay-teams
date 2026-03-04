from __future__ import annotations

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.core.models import (
    RoleDefinition,
    SubAgentInstance,
    TaskEnvelope,
    TaskRecord,
)
from agent_teams.roles.registry import RoleRegistry
from agent_teams.state.task_repo import TaskRepository


class TaskService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        instance_pool: InstancePool,
        role_registry: RoleRegistry,
    ) -> None:
        self._task_repo: TaskRepository = task_repo
        self._instance_pool: InstancePool = instance_pool
        self._role_registry: RoleRegistry = role_registry

    def submit_task(self, task: TaskEnvelope) -> str:
        _ = self._task_repo.create(task)
        return task.task_id

    def query_task(self, task_id: str) -> TaskRecord:
        return self._task_repo.get(task_id)

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        return self._task_repo.list_all()

    def create_subagent(self, role_id: str) -> SubAgentInstance:
        return self._instance_pool.create_subagent(role_id)

    def list_roles(self) -> tuple[RoleDefinition, ...]:
        return self._role_registry.list_roles()
