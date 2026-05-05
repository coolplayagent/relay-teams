# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskHandoff,
    TaskLifecyclePolicy,
    TaskSpec,
    VerificationPlan,
)
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.validation import OptionalIdentifierStr, normalize_identifier_tuple


class TaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1)
    title: str | None = None
    role_id: OptionalIdentifierStr = None
    orchestration_node_id: OptionalIdentifierStr = None
    depends_on_task_ids: tuple[str, ...] = ()
    depends_on_node_ids: tuple[str, ...] = ()
    spec: TaskSpec | None = None
    spec_artifact_id: OptionalIdentifierStr = None
    spec_source_task_id: OptionalIdentifierStr = None
    verification: VerificationPlan | None = None
    lifecycle: TaskLifecyclePolicy = Field(default_factory=TaskLifecyclePolicy)

    blocked_by_task_ids: tuple[str, ...] = ()

    @field_validator("depends_on_task_ids", "depends_on_node_ids", mode="before")
    @classmethod
    def _normalize_dependency_ids(cls, value: object) -> tuple[str, ...]:
        return normalize_identifier_tuple(value, field_name="task dependencies") or ()

    @field_validator("blocked_by_task_ids", mode="before")
    @classmethod
    def _normalize_blocked_by_ids(cls, value: object) -> tuple[str, ...]:
        return normalize_identifier_tuple(value, field_name="blocked_by_task_ids") or ()


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str | None = None
    title: str | None = None
    spec: TaskSpec | None = None
    spec_artifact_id: OptionalIdentifierStr = None
    spec_source_task_id: OptionalIdentifierStr = None
    verification: VerificationPlan | None = None
    lifecycle: TaskLifecyclePolicy | None = None
    handoff: TaskHandoff | None = None

    @model_validator(mode="after")
    def validate_non_empty_patch(self) -> TaskUpdate:
        if (
            self.objective is None
            and self.title is None
            and self.spec is None
            and self.spec_artifact_id is None
            and self.spec_source_task_id is None
            and self.verification is None
            and self.lifecycle is None
            and self.handoff is None
        ):
            raise ValueError("update must include at least one field")
        return self


class TaskExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output: str
    completion_reason: RunCompletionReason = RunCompletionReason.ASSISTANT_RESPONSE
    error_code: str | None = None
    error_message: str | None = None


class TaskOrchestrationServiceLike(Protocol):
    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: list[TaskDraft],
    ) -> dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    async def update_task_async(
        self,
        *,
        run_id: str | None,
        task_id: str,
        update: TaskUpdate,
    ) -> dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    async def list_delegated_tasks_async(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    async def list_run_tasks_async(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover

    async def dispatch_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        role_id: str,
        prompt: str = "",
    ) -> dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover


class TaskExecutionServiceLike(Protocol):
    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> object:
        raise NotImplementedError  # pragma: no cover
