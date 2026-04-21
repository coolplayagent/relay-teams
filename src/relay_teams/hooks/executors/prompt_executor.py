from __future__ import annotations

import json
from collections.abc import Callable

from relay_teams.hooks.hook_event_models import HookEventInput
from relay_teams.hooks.hook_models import HookDecision, HookHandlerConfig
from relay_teams.providers.provider_contracts import LLMRequest, LLMProvider
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.workspace import build_instance_conversation_id

_PROMPT_HOOK_SYSTEM_PROMPT = (
    "You are a Relay Teams runtime hook validator. "
    "Return exactly one JSON object matching the HookDecision schema. "
    "Do not use markdown or code fences."
)


class PromptHookExecutor:
    def __init__(
        self,
        *,
        get_role_registry: Callable[[], RoleRegistry] | None = None,
        get_provider_factory: Callable[
            [], Callable[[RoleDefinition, str | None], LLMProvider]
        ]
        | None = None,
    ) -> None:
        self._get_role_registry = get_role_registry
        self._get_provider_factory = get_provider_factory

    async def execute(
        self,
        *,
        handler: HookHandlerConfig,
        event_input: HookEventInput,
    ) -> HookDecision:
        prompt = str(handler.prompt or "").strip()
        if not prompt:
            raise ValueError("Prompt hook requires a prompt")
        role = self._resolve_prompt_role(event_input=event_input)
        provider_factory = self._require_provider_factory()
        if str(handler.model_profile or "").strip():
            role = role.model_copy(
                update={"model_profile": str(handler.model_profile).strip()}
            )
        provider = provider_factory(role, event_input.session_id)
        hook_instance_id = (
            f"hook_prompt_{event_input.event_name.value}_{event_input.run_id}".strip()
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
                workspace_id=event_input.workspace_id,
                conversation_id=conversation_id,
                instance_id=hook_instance_id,
                role_id=role.role_id,
                system_prompt=_PROMPT_HOOK_SYSTEM_PROMPT,
                user_prompt=_build_prompt_request(
                    prompt=prompt, event_input=event_input
                ),
                thinking=RunThinkingConfig(),
                runtime_hooks_enabled=False,
                persist_messages=False,
            )
        )
        return _parse_hook_decision_text(response)

    def _resolve_prompt_role(self, *, event_input: HookEventInput) -> RoleDefinition:
        role_registry = self._require_role_registry()
        role_id = str(event_input.role_id or "").strip()
        if role_id:
            try:
                return role_registry.get(role_id)
            except KeyError:
                pass
        try:
            return role_registry.get_main_agent()
        except KeyError as exc:
            raise RuntimeError(
                "Prompt hook requires an available role context"
            ) from exc

    def _require_role_registry(self) -> RoleRegistry:
        if self._get_role_registry is None:
            raise RuntimeError(
                "Prompt hook executor is not configured with a role registry"
            )
        role_registry = self._get_role_registry()
        if role_registry is None:
            raise RuntimeError(
                "Prompt hook executor could not resolve the role registry"
            )
        return role_registry

    def _require_provider_factory(
        self,
    ) -> Callable[[RoleDefinition, str | None], LLMProvider]:
        if self._get_provider_factory is None:
            raise RuntimeError(
                "Prompt hook executor is not configured with a provider factory"
            )
        return self._get_provider_factory()


def _build_prompt_request(*, prompt: str, event_input: HookEventInput) -> str:
    return (
        f"Instruction:\n{prompt.strip()}\n\n"
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
