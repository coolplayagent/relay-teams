from __future__ import annotations

import json
from typing import Protocol

from relay_teams.hooks.hook_event_models import HookEventInput
from relay_teams.hooks.hook_models import HookDecision, HookHandlerConfig


class _SubagentRunner(Protocol):
    async def run_subagent(
        self,
        *,
        run_id: str,
        session_id: str,
        workspace_id: str,
        subagent_role_id: str,
        title: str,
        prompt: str,
        suppress_hooks: bool = False,
    ) -> object: ...


class _SessionRepository(Protocol):
    def get(self, session_id: str) -> object: ...


class AgentHookExecutor:
    def __init__(
        self,
        *,
        background_task_service: _SubagentRunner,
        session_repo: _SessionRepository,
    ) -> None:
        self._background_task_service = background_task_service
        self._session_repo = session_repo

    async def execute(
        self,
        *,
        handler: HookHandlerConfig,
        event_input: HookEventInput,
    ) -> HookDecision:
        role_id = str(handler.role_id or "").strip()
        prompt_template = str(handler.prompt or "").strip()
        if not role_id:
            raise ValueError("Agent hook requires role_id")
        if not prompt_template:
            raise ValueError("Agent hook requires a prompt")
        session = self._session_repo.get(event_input.session_id)
        workspace_id = str(getattr(session, "workspace_id", "") or "").strip()
        if not workspace_id:
            raise RuntimeError("Agent hook could not resolve a workspace")
        prompt = prompt_template.replace("$ARGUMENTS", event_input.model_dump_json())
        result = await self._background_task_service.run_subagent(
            run_id=event_input.run_id,
            session_id=event_input.session_id,
            workspace_id=workspace_id,
            subagent_role_id=role_id,
            title=f"hook:{event_input.event_name.value}:{role_id}",
            prompt=prompt,
            suppress_hooks=True,
        )
        output = str(getattr(result, "output", "") or "").strip()
        if not output:
            raise RuntimeError("Agent hook returned no decision payload")
        return HookDecision.model_validate(json.loads(output))
