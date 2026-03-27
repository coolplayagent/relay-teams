# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

SKILL_CANDIDATES_HEADING = "## Skill Candidates"
SKILL_CANDIDATES_GUIDANCE = (
    "If one of these skills looks relevant, call `load_skill` before acting. "
    "You may also load other role-authorized skills when needed."
)


class UserPromptSkillCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class UserPromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str = ""
    skill_candidates: tuple[UserPromptSkillCandidate, ...] = ()


def build_user_prompt(data: UserPromptBuildInput) -> str:
    sections: list[str] = []
    objective = data.objective.strip()
    if objective:
        sections.append(objective)
    candidates_prompt = build_skill_candidates_prompt(data.skill_candidates)
    if candidates_prompt:
        sections.append(candidates_prompt)
    return "\n\n".join(section for section in sections if section.strip())


def build_skill_candidates_prompt(
    candidates: tuple[UserPromptSkillCandidate, ...],
) -> str:
    if not candidates:
        return ""
    lines = [SKILL_CANDIDATES_HEADING, SKILL_CANDIDATES_GUIDANCE]
    lines.extend(
        f"- {candidate.name}: {candidate.description}" for candidate in candidates
    )
    return "\n".join(lines)
