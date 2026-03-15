# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import override

from agent_teams.sessions.runs.models import RunThinkingConfig


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    workspace_id: str
    conversation_id: str = ""
    instance_id: str
    role_id: str
    system_prompt: str
    user_prompt: str | None
    thinking: RunThinkingConfig = RunThinkingConfig()


class LLMProvider:
    async def generate(self, _request: LLMRequest) -> str:
        raise NotImplementedError


class EchoProvider(LLMProvider):
    @override
    async def generate(self, request: LLMRequest) -> str:
        return f"ECHO: {request.user_prompt or ''}"
