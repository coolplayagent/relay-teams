from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Mapping
from json import dumps
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import JsonValue

from relay_teams.hooks.config_loader import HookConfigLoader
from relay_teams.hooks.hook_models import (
    HookDecision,
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    HookHandlerConfig,
    HookHandlerType,
    HooksConfig,
    SessionHookInput,
    StopHookInput,
    ToolHookInput,
    UserPromptSubmitHookInput,
    parse_hook_decision_payload,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent


class HookService:
    def __init__(
        self,
        *,
        config: HooksConfig | None = None,
        config_loader: HookConfigLoader | None = None,
        run_event_hub: RunEventHub | None = None,
    ) -> None:
        self._config = config or HooksConfig()
        self._config_loader = config_loader
        self._run_event_hub = run_event_hub

    def load_config(self) -> HooksConfig:
        if self._config_loader is None:
            return self._config
        self._config = self._config_loader.load()
        return self._config

    async def run_session_event(
        self,
        *,
        event_name: HookEventName,
        event: SessionHookInput,
    ) -> HookDecisionBundle:
        return await self._run_event(
            event_name=event_name,
            matcher_target="*",
            payload=cast(dict[str, JsonValue], event.model_dump(mode="json")),
            run_identity={
                "session_id": event.session_id,
                "run_id": event.run_id,
                "trace_id": event.trace_id,
            },
        )

    async def run_user_prompt_submit(
        self,
        *,
        event: UserPromptSubmitHookInput,
    ) -> HookDecisionBundle:
        return await self._run_event(
            event_name=HookEventName.USER_PROMPT_SUBMIT,
            matcher_target="*",
            payload=cast(dict[str, JsonValue], event.model_dump(mode="json")),
            run_identity={
                "session_id": event.session_id,
                "run_id": event.run_id,
                "trace_id": event.trace_id,
            },
        )

    async def run_stop_event(
        self,
        *,
        event_name: HookEventName,
        event: StopHookInput,
    ) -> HookDecisionBundle:
        return await self._run_event(
            event_name=event_name,
            matcher_target="*",
            payload=cast(dict[str, JsonValue], event.model_dump(mode="json")),
            run_identity={
                "session_id": event.session_id,
                "run_id": event.run_id,
                "trace_id": event.trace_id,
                "task_id": event.root_task_id,
            },
        )

    async def run_tool_event(
        self,
        *,
        event_name: HookEventName,
        event: ToolHookInput,
    ) -> HookDecisionBundle:
        return await self._run_event(
            event_name=event_name,
            matcher_target=event.tool_name,
            payload=cast(dict[str, JsonValue], event.model_dump(mode="json")),
            run_identity={
                "session_id": event.session_id,
                "run_id": event.run_id,
                "trace_id": event.trace_id,
                "task_id": event.task_id,
                "instance_id": event.instance_id,
                "role_id": event.role_id,
            },
        )

    async def _run_event(
        self,
        *,
        event_name: HookEventName,
        matcher_target: str,
        payload: dict[str, JsonValue],
        run_identity: Mapping[str, str],
    ) -> HookDecisionBundle:
        config = self.load_config()
        matched_handlers = 0
        current_decision = _default_decision_for(event_name)
        current_reason = ""
        current_updated_input: str | None = None
        additional_context_parts: list[str] = []
        set_env: dict[str, str] = {}
        for group in config.groups_for(event_name):
            if not _matcher_matches(group.matcher, matcher_target):
                continue
            for handler in group.hooks:
                matched_handlers += 1
                self._publish_event(
                    run_identity=run_identity,
                    event_type=RunEventType.HOOK_STARTED,
                    payload={
                        "hook_event": event_name.value,
                        "hook_handler_type": handler.type.value,
                        "hook_name": _hook_name(handler),
                    },
                )
                try:
                    decision = await self._execute_handler(
                        handler=handler,
                        event_name=event_name,
                        payload=payload,
                    )
                except Exception as exc:
                    self._publish_event(
                        run_identity=run_identity,
                        event_type=RunEventType.HOOK_FAILED,
                        payload={
                            "hook_event": event_name.value,
                            "hook_handler_type": handler.type.value,
                            "hook_name": _hook_name(handler),
                            "error": str(exc),
                        },
                    )
                    continue
                self._publish_event(
                    run_identity=run_identity,
                    event_type=RunEventType.HOOK_COMPLETED,
                    payload={
                        "hook_event": event_name.value,
                        "hook_handler_type": handler.type.value,
                        "hook_name": _hook_name(handler),
                        "decision": decision.decision.value,
                        "reason": decision.reason,
                    },
                )
                if decision.additional_context:
                    additional_context_parts.append(decision.additional_context)
                if current_updated_input is None and decision.updated_input is not None:
                    current_updated_input = decision.updated_input
                if decision.set_env:
                    for key, value in decision.set_env.items():
                        set_env[key] = value
                if _decision_priority(decision.decision) > _decision_priority(
                    current_decision
                ):
                    current_decision = decision.decision
                    current_reason = decision.reason
        bundle = HookDecisionBundle(
            decision=current_decision,
            reason=current_reason,
            updated_input=current_updated_input,
            additional_context="\n\n".join(
                part for part in additional_context_parts if part.strip()
            ),
            set_env=set_env,
            matched_handlers=matched_handlers,
        )
        if matched_handlers > 0:
            self._publish_event(
                run_identity=run_identity,
                event_type=RunEventType.HOOK_DECISION_APPLIED,
                payload={
                    "hook_event": event_name.value,
                    "decision": bundle.decision.value,
                    "reason": bundle.reason,
                    "matched_handlers": matched_handlers,
                },
            )
        return bundle

    async def _execute_handler(
        self,
        *,
        handler: HookHandlerConfig,
        event_name: HookEventName,
        payload: dict[str, JsonValue],
    ) -> HookDecision:
        if handler.type == HookHandlerType.COMMAND:
            return await self._run_command_handler(
                handler=handler,
                event_name=event_name,
                payload=payload,
            )
        if handler.type == HookHandlerType.HTTP:
            return await self._run_http_handler(
                handler=handler,
                event_name=event_name,
                payload=payload,
            )
        raise ValueError(f"Unsupported hook handler type: {handler.type.value}")

    async def _run_command_handler(
        self,
        *,
        handler: HookHandlerConfig,
        event_name: HookEventName,
        payload: dict[str, JsonValue],
    ) -> HookDecision:
        serialized = dumps(payload, ensure_ascii=False)
        completed = await asyncio.to_thread(
            subprocess.run,
            handler.command,
            input=serialized,
            capture_output=True,
            text=True,
            shell=True,
            timeout=handler.timeout_seconds,
            check=False,
        )
        stdout = completed.stdout.strip()
        if stdout:
            parsed_stdout = json.loads(stdout)
            parsed_decision = parse_hook_decision_payload(parsed_stdout)
            if parsed_decision is not None:
                return parsed_decision
        if completed.returncode != 0:
            raise RuntimeError(
                f"Hook command failed for {event_name.value} with exit code {completed.returncode}: {completed.stderr.strip()}"
            )
        return HookDecision(decision=_default_decision_for(event_name))

    async def _run_http_handler(
        self,
        *,
        handler: HookHandlerConfig,
        event_name: HookEventName,
        payload: dict[str, JsonValue],
    ) -> HookDecision:
        serialized = dumps(payload, ensure_ascii=False).encode("utf-8")

        def _request() -> HookDecision:
            request = Request(
                url=handler.url,
                method="POST",
                data=serialized,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    **handler.headers,
                },
            )
            try:
                with urlopen(request, timeout=handler.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
            except HTTPError as exc:
                raise RuntimeError(
                    f"HTTP hook failed for {event_name.value}: {exc.code}"
                ) from exc
            except URLError as exc:
                raise RuntimeError(
                    f"HTTP hook failed for {event_name.value}: {exc.reason}"
                ) from exc
            if not body.strip():
                return HookDecision(decision=_default_decision_for(event_name))
            parsed_body = json.loads(body)
            parsed_decision = parse_hook_decision_payload(parsed_body)
            if parsed_decision is None:
                return HookDecision(decision=_default_decision_for(event_name))
            return parsed_decision

        return await asyncio.to_thread(_request)

    def _publish_event(
        self,
        *,
        run_identity: Mapping[str, str],
        event_type: RunEventType,
        payload: dict[str, JsonValue],
    ) -> None:
        if self._run_event_hub is None:
            return
        session_id = str(run_identity.get("session_id") or "").strip()
        run_id = str(run_identity.get("run_id") or "").strip()
        trace_id = str(run_identity.get("trace_id") or run_id).strip()
        if not session_id or not run_id or not trace_id:
            return
        self._run_event_hub.publish(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=trace_id,
                task_id=_optional_identity(run_identity, "task_id"),
                instance_id=_optional_identity(run_identity, "instance_id"),
                role_id=_optional_identity(run_identity, "role_id"),
                event_type=event_type,
                payload_json=dumps(payload, ensure_ascii=False),
            )
        )


def _optional_identity(run_identity: Mapping[str, str], key: str) -> str | None:
    value = str(run_identity.get(key) or "").strip()
    return value or None


def _matcher_matches(matcher: str, matcher_target: str) -> bool:
    normalized_matcher = matcher.strip()
    if not normalized_matcher or normalized_matcher == "*":
        return True
    return normalized_matcher == matcher_target


def _default_decision_for(event_name: HookEventName) -> HookDecisionType:
    if event_name in {
        HookEventName.POST_TOOL_USE,
        HookEventName.POST_TOOL_USE_FAILURE,
        HookEventName.SESSION_END,
        HookEventName.STOP_FAILURE,
    }:
        return HookDecisionType.CONTINUE
    return HookDecisionType.ALLOW


def _decision_priority(value: HookDecisionType) -> int:
    if value == HookDecisionType.DENY:
        return 4
    if value == HookDecisionType.ASK:
        return 3
    if value == HookDecisionType.RETRY:
        return 2
    if value == HookDecisionType.ALLOW:
        return 1
    return 0


def _hook_name(handler: HookHandlerConfig) -> str:
    if handler.name.strip():
        return handler.name
    if handler.type == HookHandlerType.COMMAND:
        return handler.command
    return handler.url
