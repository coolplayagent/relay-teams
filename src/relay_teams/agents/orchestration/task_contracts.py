# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable
from typing import Dict, List, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason


class TaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1)
    title: Optional[str] = None


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: Optional[str] = None
    title: Optional[str] = None

    @model_validator(mode="after")
    def validate_non_empty_patch(self) -> TaskUpdate:
        if self.objective is None and self.title is None:
            raise ValueError("update must include at least one field")
        return self


class TaskExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output: str
    completion_reason: RunCompletionReason = RunCompletionReason.ASSISTANT_RESPONSE
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class TaskOrchestrationServiceLike(Protocol):
    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: List[TaskDraft],
    ) -> Dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    def update_task(
        self,
        *,
        run_id: Optional[str],
        task_id: str,
        update: TaskUpdate,
    ) -> Dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    async def update_task_async(
        self,
        *,
        run_id: Optional[str],
        task_id: str,
        update: TaskUpdate,
    ) -> Dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    def list_delegated_tasks(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> Dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    async def list_delegated_tasks_async(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> Dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    def list_run_tasks(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> Dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    async def list_run_tasks_async(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> Dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    def dispatch_task(
        self,
        *,
        run_id: Optional[str],
        task_id: str,
        role_id: str,
        prompt: str = "",
    ) -> Awaitable[Dict[str, JsonValue]]:
        raise NotImplementedError  # pragma: no cover


class TaskExecutionServiceLike(Protocol):
    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: Optional[str] = None,
    ) -> object:
        raise NotImplementedError  # pragma: no cover
