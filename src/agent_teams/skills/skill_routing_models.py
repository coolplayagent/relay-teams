# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.sessions.runs.run_models import RuntimePromptConversationContext


class SkillRoutingMode(str, Enum):
    PASSTHROUGH = "passthrough"
    SEARCH = "search"
    FALLBACK = "fallback"


class SkillRoutingFallbackReason(str, Enum):
    EMPTY_QUERY = "empty_query"
    NO_HITS = "no_hits"
    SEARCH_FAILED = "search_failed"


class SkillRoutingContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str = ""
    role_name: str = Field(min_length=1)
    role_description: str = ""
    shared_state_snapshot: tuple[tuple[str, str], ...] = ()
    conversation_context: RuntimePromptConversationContext | None = None
    orchestration_prompt: str = ""


class SkillRouteCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_name: str = Field(min_length=1)
    score: float
    rank: int = Field(ge=1)
    snippet: str = ""


class SkillRoutingDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: SkillRoutingMode
    query_text: str = ""
    authorized_count: int = Field(ge=0)
    visible_skills: tuple[str, ...] = ()
    candidates: tuple[SkillRouteCandidate, ...] = ()
    fallback_reason: SkillRoutingFallbackReason | None = None


class SkillRoutingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorized_skills: tuple[str, ...] = ()
    visible_skills: tuple[str, ...] = ()
    diagnostics: SkillRoutingDiagnostics


class SkillPromptResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_prompt: str = ""
    routing: SkillRoutingResult
