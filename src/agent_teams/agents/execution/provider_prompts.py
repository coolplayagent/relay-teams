# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PromptSkillInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    instructions: str = Field(min_length=1)


class ProviderPromptAugmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: str = Field(min_length=1)
    allowed_tools: tuple[str, ...]
    skill_instructions: tuple[PromptSkillInstruction, ...] = ()


def build_tool_rules_prompt(allowed_tools: tuple[str, ...]) -> str:
    tools_line = ", ".join(allowed_tools) if allowed_tools else "none"
    return f"## Tool Rules\n- Available tools: {tools_line}."


def build_skill_instructions_prompt(
    skill_instructions: tuple[PromptSkillInstruction, ...],
) -> str:
    if not skill_instructions:
        return ""
    skill_blocks = [
        f"### Skill: {entry.name}\n{entry.instructions}" for entry in skill_instructions
    ]
    return "## Skill Instructions\n" + "\n\n".join(skill_blocks)


def build_provider_augmented_system_prompt(data: ProviderPromptAugmentInput) -> str:
    sections: list[str] = [
        data.system_prompt,
        build_tool_rules_prompt(data.allowed_tools),
    ]
    skill_prompt = build_skill_instructions_prompt(data.skill_instructions)
    if skill_prompt:
        sections.append(skill_prompt)
    return "\n\n".join(sections)
