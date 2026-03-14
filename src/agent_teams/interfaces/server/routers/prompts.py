# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Annotated, ClassVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_teams.interfaces.server.deps import (
    get_mcp_registry,
    get_role_registry,
    get_skill_registry,
    get_tool_registry,
)
from agent_teams.agents.execution.system_prompts import (
    PromptSkillInstruction,
    RuntimePromptBuildInput,
    RuntimePromptBuilder,
    SystemPromptBuildInput,
    build_system_prompt,
)
from agent_teams.agents.execution.user_prompts import (
    UserPromptBuildInput,
    build_user_prompt,
)
from agent_teams.mcp.registry import McpRegistry
from agent_teams.roles.registry import RoleRegistry

from agent_teams.skills.registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry

router = APIRouter(prefix="/prompts", tags=["Prompts"])


class PromptPreviewRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    objective: str | None = None
    shared_state: dict[str, JsonValue] = Field(default_factory=dict)
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


@router.post(":preview", response_model=PromptPreviewResponse)
async def preview_prompts(
    req: PromptPreviewRequest,
    role_registry: Annotated[RoleRegistry, Depends(get_role_registry)],
    tool_registry: Annotated[ToolRegistry, Depends(get_tool_registry)],
    mcp_registry: Annotated[McpRegistry, Depends(get_mcp_registry)],
    skill_registry: Annotated[SkillRegistry, Depends(get_skill_registry)],
) -> PromptPreviewResponse:
    try:
        role = role_registry.get(req.role_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    resolved_tools = req.tools if req.tools is not None else role.tools
    resolved_skills = req.skills if req.skills is not None else role.skills
    objective = req.objective.strip() if req.objective else ""

    try:
        tool_registry.validate_known(resolved_tools)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        skill_registry.validate_known(resolved_skills)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    runtime_system_prompt = await RuntimePromptBuilder(
        role_registry=role_registry,
        mcp_registry=mcp_registry,
    ).build(
        RuntimePromptBuildInput(
            role=role,
            shared_state_snapshot=_to_shared_state_snapshot(req.shared_state),
        )
    )
    skill_instructions = tuple(
        PromptSkillInstruction(name=entry.name, description=entry.description)
        for entry in skill_registry.get_instruction_entries(resolved_skills)
    )
    provider_system_prompt = build_system_prompt(
        SystemPromptBuildInput(
            system_prompt=runtime_system_prompt,
            allowed_tools=resolved_tools,
            skill_instructions=skill_instructions,
        )
    )
    user_prompt = (
        build_user_prompt(UserPromptBuildInput(objective=objective))
        if objective
        else ""
    )

    return PromptPreviewResponse(
        role_id=role.role_id,
        objective=objective,
        tools=resolved_tools,
        skills=resolved_skills,
        runtime_system_prompt=runtime_system_prompt,
        provider_system_prompt=provider_system_prompt,
        user_prompt=user_prompt,
    )


def _to_shared_state_snapshot(
    shared_state: dict[str, JsonValue],
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
