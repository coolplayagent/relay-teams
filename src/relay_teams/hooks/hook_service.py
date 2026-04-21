from __future__ import annotations

import logging
import time
from json import dumps

from pydantic import JsonValue

from relay_teams.hooks.executors.command_executor import CommandHookExecutor
from relay_teams.hooks.executors.http_executor import HttpHookExecutor
from relay_teams.hooks.hook_event_models import HookEventInput, PreToolUseInput
from relay_teams.hooks.hook_loader import HookLoader
from relay_teams.hooks.hook_matcher import hook_matches_event
from relay_teams.hooks.hook_models import (
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    HookExecutionResult,
    HookExecutionStatus,
    HookHandlerConfig,
    HookHandlerType,
    HookRuntimeSnapshot,
    HookRuntimeView,
    HookSourceInfo,
    HooksConfig,
    LoadedHookRuntimeView,
)
from relay_teams.hooks.hook_runtime_state import HookRuntimeState
from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent

LOGGER = get_logger(__name__)


class HookService:
    def __init__(
        self,
        *,
        loader: HookLoader,
        runtime_state: HookRuntimeState,
        command_executor: CommandHookExecutor,
        http_executor: HttpHookExecutor,
    ) -> None:
        self._loader = loader
        self._runtime_state = runtime_state
        self._command_executor = command_executor
        self._http_executor = http_executor

    def get_user_config(self) -> HooksConfig:
        return self._loader.get_user_config()

    def get_effective_config(self) -> HookRuntimeSnapshot:
        return self._loader.load_snapshot()

    def get_runtime_view(self) -> HookRuntimeView:
        snapshot = self._loader.load_snapshot()
        loaded_hooks: list[LoadedHookRuntimeView] = []
        for event_name, groups in snapshot.hooks.items():
            for resolved in groups:
                for handler in resolved.group.hooks:
                    loaded_hooks.append(
                        LoadedHookRuntimeView(
                            name=(
                                handler.name
                                or handler.command
                                or handler.url
                                or handler.type.value
                            ),
                            handler_type=handler.type,
                            event_name=event_name,
                            matcher=resolved.group.matcher,
                            if_condition=resolved.group.if_condition,
                            tool_names=resolved.group.tool_names,
                            role_ids=resolved.group.role_ids,
                            session_modes=resolved.group.session_modes,
                            run_kinds=resolved.group.run_kinds,
                            timeout_seconds=handler.timeout_seconds,
                            run_async=handler.run_async,
                            on_error=handler.on_error,
                            source=resolved.source,
                        )
                    )
        return HookRuntimeView(
            sources=snapshot.sources,
            loaded_hooks=tuple(loaded_hooks),
        )

    def save_user_config(self, payload: object) -> HooksConfig:
        config = self._loader.validate_payload(payload)
        self._loader.save_user_config(config)
        return config

    def validate_config(self, payload: object) -> HooksConfig:
        return self._loader.validate_payload(payload)

    def snapshot_run(self, run_id: str) -> HookRuntimeSnapshot:
        snapshot = self._loader.load_snapshot()
        self._runtime_state.set_snapshot(run_id, snapshot)
        return snapshot

    def clear_run(self, run_id: str) -> None:
        self._runtime_state.clear(run_id)

    def get_run_env(self, run_id: str) -> dict[str, str]:
        return self._runtime_state.get_env(run_id)

    async def execute(
        self,
        *,
        event_input: HookEventInput,
        run_event_hub: RunEventHub | None,
    ) -> HookDecisionBundle:
        snapshot = self._runtime_state.get_snapshot(event_input.run_id)
        if snapshot is None:
            snapshot = self.snapshot_run(event_input.run_id)
        matches = []
        tool_name = ""
        if isinstance(event_input, PreToolUseInput):
            tool_name = event_input.tool_name
        elif hasattr(event_input, "tool_name"):
            tool_name = str(getattr(event_input, "tool_name") or "")
        for resolved in snapshot.hooks.get(event_input.event_name, ()):
            if hook_matches_event(resolved.group, event_input, tool_name=tool_name):
                matches.append(resolved)
                _publish_hook_event(
                    run_event_hub=run_event_hub,
                    event_input=event_input,
                    event_type=RunEventType.HOOK_MATCHED,
                    payload={
                        "hook_event": event_input.event_name.value,
                        "hook_source": resolved.source.scope.value,
                        "hook_path": str(resolved.source.path),
                        "matcher": resolved.group.matcher,
                        "tool_name": tool_name,
                    },
                )
        executions: list[HookExecutionResult] = []
        for resolved in matches:
            for handler in resolved.group.hooks:
                executions.append(
                    await self._execute_handler(
                        event_input=event_input,
                        handler=handler,
                        source=resolved.source,
                        run_event_hub=run_event_hub,
                    )
                )
        bundle = _merge_decisions(event_input.event_name, executions)
        if bundle.set_env:
            self._runtime_state.set_env(event_input.run_id, bundle.set_env)
        if bundle.executions:
            _publish_hook_event(
                run_event_hub=run_event_hub,
                event_input=event_input,
                event_type=RunEventType.HOOK_DECISION_APPLIED,
                payload={
                    "hook_event": event_input.event_name.value,
                    "decision": bundle.decision.value,
                    "reason": bundle.reason,
                    "additional_context": list(bundle.additional_context),
                },
            )
        return bundle

    async def _execute_handler(
        self,
        *,
        event_input: HookEventInput,
        handler: HookHandlerConfig,
        source: HookSourceInfo,
        run_event_hub: RunEventHub | None,
    ) -> HookExecutionResult:
        started = time.perf_counter()
        handler_name = (
            handler.name
            or (
                handler.command
                if handler.type == HookHandlerType.COMMAND
                else handler.url
            )
            or handler.type.value
        )
        _publish_hook_event(
            run_event_hub=run_event_hub,
            event_input=event_input,
            event_type=RunEventType.HOOK_STARTED,
            payload={
                "hook_event": event_input.event_name.value,
                "hook_source": getattr(source, "scope").value,
                "hook_name": handler_name,
                "hook_handler_type": handler.type.value,
            },
        )
        try:
            if handler.type == HookHandlerType.COMMAND:
                decision = await self._command_executor.execute(
                    handler=handler,
                    event_input=event_input,
                )
            else:
                decision = await self._http_executor.execute(
                    handler=handler,
                    event_input=event_input,
                )
            result = HookExecutionResult(
                source=source,
                event_name=event_input.event_name,
                handler_name=handler_name,
                handler_type=handler.type,
                status=HookExecutionStatus.COMPLETED,
                decision=decision,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            _publish_hook_event(
                run_event_hub=run_event_hub,
                event_input=event_input,
                event_type=RunEventType.HOOK_COMPLETED,
                payload={
                    "hook_event": event_input.event_name.value,
                    "hook_source": source.scope.value,
                    "hook_name": handler_name,
                    "hook_handler_type": handler.type.value,
                    "decision": decision.decision.value,
                    "reason": decision.reason,
                    "duration_ms": result.duration_ms,
                },
            )
            return result
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                LOGGER,
                logging.WARNING,
                event="hooks.execution.failed",
                message="Hook execution failed",
                payload={
                    "hook_event": event_input.event_name.value,
                    "hook_name": handler_name,
                    "handler_type": handler.type.value,
                },
                exc_info=exc,
            )
            _publish_hook_event(
                run_event_hub=run_event_hub,
                event_input=event_input,
                event_type=RunEventType.HOOK_FAILED,
                payload={
                    "hook_event": event_input.event_name.value,
                    "hook_source": source.scope.value,
                    "hook_name": handler_name,
                    "hook_handler_type": handler.type.value,
                    "reason": str(exc),
                    "duration_ms": duration_ms,
                },
            )
            return HookExecutionResult(
                source=source,
                event_name=event_input.event_name,
                handler_name=handler_name,
                handler_type=handler.type,
                status=HookExecutionStatus.FAILED,
                error=str(exc),
                duration_ms=duration_ms,
            )


def _merge_decisions(
    event_name: HookEventName,
    executions: list[HookExecutionResult],
) -> HookDecisionBundle:
    decisions = [item.decision for item in executions if item.decision is not None]
    if not decisions:
        return HookDecisionBundle(
            decision=_default_decision(event_name),
            executions=tuple(executions),
        )
    winner = _default_decision(event_name)
    reason = ""
    updated_input: JsonValue | None = None
    additional_context: list[str] = []
    set_env: dict[str, str] = {}
    deferred_action = ""
    if any(item.decision == HookDecisionType.DENY for item in decisions):
        winner = HookDecisionType.DENY
    elif any(item.decision == HookDecisionType.ASK for item in decisions):
        winner = HookDecisionType.ASK
    elif event_name == HookEventName.STOP and any(
        item.decision == HookDecisionType.RETRY for item in decisions
    ):
        winner = HookDecisionType.RETRY
    elif any(item.decision == HookDecisionType.UPDATED_INPUT for item in decisions):
        winner = HookDecisionType.UPDATED_INPUT
    elif any(item.decision == HookDecisionType.SET_ENV for item in decisions):
        winner = HookDecisionType.SET_ENV
    elif any(
        item.decision == HookDecisionType.ADDITIONAL_CONTEXT for item in decisions
    ):
        winner = HookDecisionType.ADDITIONAL_CONTEXT
    elif any(item.decision == HookDecisionType.DEFER for item in decisions):
        winner = HookDecisionType.DEFER
    for item in decisions:
        if item.reason and not reason:
            reason = item.reason
        if item.updated_input is not None and updated_input is None:
            updated_input = item.updated_input
        additional_context.extend(text for text in item.additional_context if text)
        set_env.update(item.set_env)
        if item.deferred_action and not deferred_action:
            deferred_action = item.deferred_action
    return HookDecisionBundle(
        decision=winner,
        reason=reason,
        updated_input=updated_input,
        additional_context=tuple(additional_context),
        set_env=set_env,
        deferred_action=deferred_action,
        executions=tuple(executions),
    )


def _default_decision(event_name: HookEventName) -> HookDecisionType:
    if event_name in {
        HookEventName.POST_TOOL_USE,
        HookEventName.POST_TOOL_USE_FAILURE,
    }:
        return HookDecisionType.CONTINUE
    if event_name == HookEventName.SESSION_END:
        return HookDecisionType.OBSERVE
    return HookDecisionType.ALLOW


def _publish_hook_event(
    *,
    run_event_hub: RunEventHub | None,
    event_input: HookEventInput,
    event_type: RunEventType,
    payload: dict[str, JsonValue],
) -> None:
    if run_event_hub is None:
        return
    run_event_hub.publish(
        RunEvent(
            session_id=event_input.session_id,
            run_id=event_input.run_id,
            trace_id=event_input.trace_id,
            task_id=event_input.task_id,
            instance_id=event_input.instance_id,
            role_id=event_input.role_id,
            event_type=event_type,
            payload_json=dumps(payload, ensure_ascii=False),
        )
    )
