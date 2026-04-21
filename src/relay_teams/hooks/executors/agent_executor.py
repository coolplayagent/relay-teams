from __future__ import annotations

import json
from collections.abc import Callable

from relay_teams.hooks.hook_event_models import HookEventInput
from relay_teams.hooks.hook_models import HookDecision, HookHandlerConfig
from relay_teams.providers.provider_contracts import LLMRequest, LLMProvider
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.workspace import build_instance_conversation_id

_AGENT_HOOK_APPENDIX = (
    "\n\nYou are acting as a Relay Teams runtime hook verifier. "
    "Return exactly one JSON object matching the HookDecision schema. "
    "Do not use markdown or code fences."
)


class AgentHookExecutor:
    def __init__(
        self,
        *,
        get_role_registry: Callable[[], RoleRegistry] | None = None,
        get_provider_factory: Callable[
            [], Callable[[RoleDefinition, str | None], LLMProvider]
        ]
        | None = None,
        get_session_repo: Callable[[], SessionRepository] | None = None,
    ) -> None:
        self._get_role_registry = get_role_registry
        self._get_provider_factory = get_provider_factory
        self._get_session_repo = get_session_repo

    async def execute(
        self,
        *,
        handler: HookHandlerConfig,
        event_input: HookEventInput,
    ) -> HookDecision:
        role_id = str(handler.role_id or "").strip()
        if not role_id:
            raise ValueError("Agent hook requires a role_id")
        role_registry = self._require_role_registry()
        role = role_registry.get(role_id)
        if str(handler.model_profile or "").strip():
            role = role.model_copy(
                update={"model_profile": str(handler.model_profile).strip()}
            )
        provider_factory = self._require_provider_factory()
        provider = provider_factory(role, event_input.session_id)
        workspace_id = event_input.workspace_id.strip()
        if not workspace_id and self._get_session_repo is not None:
            try:
                workspace_id = (
                    self._get_session_repo().get(event_input.session_id).workspace_id
                )
            except Exception:
                workspace_id = ""
        hook_instance_id = (
            f"hook_agent_{event_input.event_name.value}_{event_input.run_id}".strip()
        )
        conversation_id = build_instance_conversation_id(
            event_input.session_id,
            role.role_id,
            hook_instance_id,
        )
        response = await provider.generate(
            LLMRequest(
                run_id=event_input.run_id,
                trace_id=event_input.trace_id,
                task_id=event_input.task_id or f"hook-{event_input.event_name.value}",
                session_id=event_input.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                instance_id=hook_instance_id,
                role_id=role.role_id,
                system_prompt=f"{role.system_prompt.rstrip()}{_AGENT_HOOK_APPENDIX}",
                user_prompt=_build_agent_request(
                    handler=handler, event_input=event_input
                ),
                thinking=RunThinkingConfig(),
                runtime_hooks_enabled=False,
                persist_messages=False,
            )
        )
        return _parse_hook_decision_text(response)

    def _require_role_registry(self) -> RoleRegistry:
        if self._get_role_registry is None:
            raise RuntimeError(
                "Agent hook executor is not configured with a role registry"
            )
        role_registry = self._get_role_registry()
        if role_registry is None:
            raise RuntimeError(
                "Agent hook executor could not resolve the role registry"
            )
        return role_registry

    def _require_provider_factory(
        self,
    ) -> Callable[[RoleDefinition, str | None], LLMProvider]:
        if self._get_provider_factory is None:
            raise RuntimeError(
                "Agent hook executor is not configured with a provider factory"
            )
        return self._get_provider_factory()


def _build_agent_request(
    *, handler: HookHandlerConfig, event_input: HookEventInput
) -> str:
    instruction = (
        str(handler.prompt or "").strip()
        or "Review the runtime hook event and return the correct HookDecision."
    )
    return (
        f"Instruction:\n{instruction}\n\n"
        "Return a JSON object with keys: decision, reason, updated_input, additional_context, set_env, deferred_action.\n"
        f"Event payload:\n{event_input.model_dump_json(indent=2)}"
    )


def _parse_hook_decision_text(raw_text: str) -> HookDecision:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return HookDecision.model_validate(json.loads(text))
