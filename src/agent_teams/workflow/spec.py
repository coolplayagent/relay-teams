# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorkflowTaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()


class WorkflowTaskTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_name: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    objective_template: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()

    def render(self, *, objective: str) -> WorkflowTaskSpec:
        return WorkflowTaskSpec(
            task_name=self.task_name,
            objective=self.objective_template.format(objective=objective),
            role_id=self.role_id,
            depends_on=self.depends_on,
        )


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(default="")
    selection_hints: tuple[str, ...] = ()
    is_default: bool = False
    tasks: tuple[WorkflowTaskTemplate, ...] = ()
    guidance: str = Field(default="")

    def instantiate(self, *, objective: str) -> tuple[WorkflowTaskSpec, ...]:
        return tuple(task.render(objective=objective) for task in self.tasks)


class WorkflowRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    workflow_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    matched_hints: tuple[str, ...] = ()
    guidance: str = Field(default="")
    is_default: bool = False
