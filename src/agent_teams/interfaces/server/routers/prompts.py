# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Annotated, ClassVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_teams.interfaces.server.deps import (
    get_role_registry,
    get_skill_registry,
    get_tool_registry,
)
from agent_teams.agents.execution.provider_prompts import (
    PromptSkillInstruction,
    ProviderPromptAugmentInput,
    build_provider_augmented_system_prompt,
    build_skill_instructions_prompt,
    build_tool_rules_prompt,
)
from agent_teams.agents.execution.runtime_prompts import (
    RuntimePromptBuildInput,
    build_runtime_system_prompt,
)
from agent_teams.agents.execution.user_prompts import (
    UserPromptBuildInput,
    build_user_prompt,
)
from agent_teams.roles.registry import RoleRegistry
from agent_teams.shared_types.json_types import JsonObject, JsonValue
from agent_teams.skills.registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan

router = APIRouter(prefix="/prompts", tags=["Prompts"])

DEFAULT_PREVIEW_OBJECTIVE = "Preview objective placeholder"


class PromptPreviewRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    objective: str | None = None
    shared_state: JsonObject = Field(default_factory=dict)
    tools: tuple[str, ...] | None = None
    skills: tuple[str, ...] | None = None


class PromptPreviewResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    role_id: str
    objective: str
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    runtime_system_prompt: str
    provider_system_prompt: str
    user_prompt: str
    tool_prompt: str
    skill_prompt: str


@router.post(":preview", response_model=PromptPreviewResponse)
def preview_prompts(
    req: PromptPreviewRequest,
    role_registry: Annotated[RoleRegistry, Depends(get_role_registry)],
    tool_registry: Annotated[ToolRegistry, Depends(get_tool_registry)],
    skill_registry: Annotated[SkillRegistry, Depends(get_skill_registry)],
) -> PromptPreviewResponse:
    try:
        role = role_registry.get(req.role_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    resolved_tools = req.tools if req.tools is not None else role.tools
    resolved_skills = req.skills if req.skills is not None else role.skills
    objective = req.objective.strip() if req.objective else DEFAULT_PREVIEW_OBJECTIVE
    if not objective:
        objective = DEFAULT_PREVIEW_OBJECTIVE

    try:
        tool_registry.validate_known(resolved_tools)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        skill_registry.validate_known(resolved_skills)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    runtime_system_prompt = build_runtime_system_prompt(
        RuntimePromptBuildInput(
            role=role,
            task=_build_preview_task(objective=objective),
            shared_state_snapshot=_to_shared_state_snapshot(req.shared_state),
        )
    )
    skill_instructions = tuple(
        PromptSkillInstruction(name=entry.name, instructions=entry.instructions)
        for entry in skill_registry.get_instruction_entries(resolved_skills)
    )
    tool_prompt = build_tool_rules_prompt(resolved_tools)
    skill_prompt = build_skill_instructions_prompt(skill_instructions)
    provider_system_prompt = build_provider_augmented_system_prompt(
        ProviderPromptAugmentInput(
            system_prompt=runtime_system_prompt,
            allowed_tools=resolved_tools,
            skill_instructions=skill_instructions,
        )
    )
    user_prompt = build_user_prompt(UserPromptBuildInput(objective=objective))

    return PromptPreviewResponse(
        role_id=role.role_id,
        objective=objective,
        tools=resolved_tools,
        skills=resolved_skills,
        runtime_system_prompt=runtime_system_prompt,
        provider_system_prompt=provider_system_prompt,
        user_prompt=user_prompt,
        tool_prompt=tool_prompt,
        skill_prompt=skill_prompt,
    )


def _build_preview_task(*, objective: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="prompt-preview-task",
        session_id="prompt-preview-session",
        parent_task_id=None,
        trace_id="prompt-preview-trace",
        objective=objective,
        verification=VerificationPlan(checklist=("prompt_preview",)),
    )


def _to_shared_state_snapshot(
    shared_state: JsonObject,
) -> tuple[tuple[str, str], ...]:
    normalized_items = [
        (str(key), _json_value_to_text(value)) for key, value in shared_state.items()
    ]
    normalized_items.sort(key=lambda item: item[0])
    return tuple(normalized_items)


def _json_value_to_text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
