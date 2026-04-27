from __future__ import annotations

import asyncio
import logging
import time
from json import dumps
from typing import Optional, Protocol

from pydantic import JsonValue

from relay_teams.hooks.executors.command_executor import CommandHookExecutor
from relay_teams.hooks.executors.agent_executor import AgentHookExecutor
from relay_teams.hooks.executors.http_executor import (
    HttpHookExecutor,
    NonBlockingHttpHookError,
)
from relay_teams.hooks.executors.prompt_executor import PromptHookExecutor
from relay_teams.hooks.hook_conditions import hook_handler_condition_matches
from relay_teams.hooks.hook_event_models import (
    HookEventInput,
    PermissionDeniedInput,
    PermissionRequestInput,
    PostToolUseFailureInput,
    PostToolUseInput,
    PreToolUseInput,
)
from relay_teams.hooks.hook_loader import HookLoader
from relay_teams.hooks.hook_matcher import hook_matches_event
from relay_teams.hooks.hook_models import (
    HookDecision,
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    HookExecutionResult,
    HookExecutionStatus,
    HookHandlerConfig,
    HookHandlerType,
    HookOnError,
    HookRuntimeSnapshot,
    HookRuntimeView,
    HookSourceInfo,
    HooksConfig,
    LoadedHookRuntimeView,
    ResolvedHookMatcherGroup,
)
from relay_teams.hooks.hook_runtime_state import HookRuntimeState
from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub, publish_run_event_async
from relay_teams.sessions.runs.run_models import RunEvent

LOGGER = get_logger(__name__)
_OBSERVE_ONLY_EVENTS = frozenset(
    {
        HookEventName.SESSION_END,
        HookEventName.STOP_FAILURE,
        HookEventName.SUBAGENT_START,
        HookEventName.POST_COMPACT,
        HookEventName.NOTIFICATION,
        HookEventName.INSTRUCTIONS_LOADED,
    }
)


class _RunInjectionManager(Protocol):
    def is_active(self, run_id: str) -> bool:
        raise NotImplementedError

    def enqueue(
        self,
        run_id: str,
        recipient_instance_id: str,
        source: InjectionSource,
        content: str,
        sender_instance_id: Optional[str] = None,
        sender_role_id: Optional[str] = None,
    ) -> object:
        raise NotImplementedError


class HookService:
    def __init__(
        self,
        *,
        loader: HookLoader,
        runtime_state: HookRuntimeState,
        command_executor: CommandHookExecutor,
        http_executor: HttpHookExecutor,
        prompt_executor: PromptHookExecutor | None = None,
        agent_executor: AgentHookExecutor | None = None,
    ) -> None:
        self._loader = loader
        self._runtime_state = runtime_state
        self._command_executor = command_executor
        self._http_executor = http_executor
        self._prompt_executor = prompt_executor
        self._agent_executor = agent_executor
        self._injection_manager: _RunInjectionManager | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()

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
                                resolved.group.name
                                or handler.name
                                or handler.command
                                or handler.url
                                or handler.prompt
                                or handler.role_id
                                or handler.type.value
                            ),
                            handler_type=handler.type,
                            event_name=event_name,
                            matcher=resolved.group.matcher,
                            if_rule=handler.if_rule,
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

    def set_run_snapshot(self, run_id: str, snapshot: HookRuntimeSnapshot) -> None:
        self._runtime_state.set_snapshot(run_id, snapshot)

    def set_prompt_executor(self, executor: PromptHookExecutor | None) -> None:
        self._prompt_executor = executor

    def set_agent_executor(self, executor: AgentHookExecutor | None) -> None:
        self._agent_executor = executor

    def set_injection_manager(
        self,
        injection_manager: _RunInjectionManager | None,
    ) -> None:
        self._injection_manager = injection_manager

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
        elif isinstance(event_input, PermissionDeniedInput):
            tool_name = event_input.tool_name
        elif isinstance(event_input, PermissionRequestInput):
            tool_name = event_input.tool_name
        elif isinstance(event_input, PostToolUseInput):
            tool_name = event_input.tool_name
        elif isinstance(event_input, PostToolUseFailureInput):
            tool_name = event_input.tool_name
        for resolved in snapshot.hooks.get(event_input.event_name, ()):
            if hook_matches_event(resolved.group, event_input, tool_name=tool_name):
                matches.append(resolved)
                await _publish_hook_event_async(
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
        sync_invocations: list[tuple[ResolvedHookMatcherGroup, HookHandlerConfig]] = []
        seen_sync_keys: set[tuple[str, ...]] = set()
        seen_async_keys: set[tuple[str, ...]] = set()
        for resolved in matches:
            for handler in resolved.group.hooks:
                if not hook_handler_condition_matches(
                    if_rule=handler.if_rule,
                    event_input=event_input,
                    tool_name=tool_name,
                ):
                    continue
                dedup_key = _handler_dedup_key(handler)
                if handler.run_async:
                    if dedup_key is not None and dedup_key in seen_async_keys:
                        continue
                    if dedup_key is not None:
                        seen_async_keys.add(dedup_key)
                    self._schedule_async_handler(
                        event_input=event_input,
                        handler=handler,
                        source=resolved.source,
                        run_event_hub=run_event_hub,
                    )
                    continue
                if dedup_key is not None and dedup_key in seen_sync_keys:
                    continue
                if dedup_key is not None:
                    seen_sync_keys.add(dedup_key)
                sync_invocations.append((resolved, handler))
        if sync_invocations:
            if any(
                handler.on_error == HookOnError.FAIL for _, handler in sync_invocations
            ):
                for resolved, handler in sync_invocations:
                    executions.append(
                        await self._execute_handler(
                            event_input=event_input,
                            handler=handler,
                            source=resolved.source,
                            run_event_hub=run_event_hub,
                        )
                    )
            else:
                executions.extend(
                    await asyncio.gather(
                        *(
                            self._execute_handler(
                                event_input=event_input,
                                handler=handler,
                                source=resolved.source,
                                run_event_hub=run_event_hub,
                            )
                            for resolved, handler in sync_invocations
                        )
                    )
                )
        bundle = _merge_decisions(event_input.event_name, executions)
        if bundle.set_env:
            self._runtime_state.set_env(event_input.run_id, bundle.set_env)
        if bundle.executions:
            conflicts = _decision_conflicts(bundle.executions)
            if conflicts:
                await _publish_hook_event_async(
                    run_event_hub=run_event_hub,
                    event_input=event_input,
                    event_type=RunEventType.HOOK_CONFLICT,
                    payload={
                        "hook_event": event_input.event_name.value,
                        "decision": bundle.decision.value,
                        "conflicts": list(conflicts),
                    },
                )
            await _publish_hook_event_async(
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

    def _schedule_async_handler(
        self,
        *,
        event_input: HookEventInput,
        handler: HookHandlerConfig,
        source: HookSourceInfo,
        run_event_hub: RunEventHub | None,
    ) -> None:
        async def run_background_hook() -> None:
            try:
                result = await self._execute_handler(
                    event_input=event_input,
                    handler=handler,
                    source=source,
                    run_event_hub=run_event_hub,
                )
                await self._apply_async_rewake(
                    event_input=event_input,
                    handler=handler,
                    result=result,
                    run_event_hub=run_event_hub,
                )
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="hooks.async_execution.failed",
                    message="Async hook execution failed",
                    payload={
                        "hook_event": event_input.event_name.value,
                        "handler_type": handler.type.value,
                    },
                    exc_info=exc,
                )

        task = asyncio.create_task(run_background_hook())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _apply_async_rewake(
        self,
        *,
        event_input: HookEventInput,
        handler: HookHandlerConfig,
        result: HookExecutionResult,
        run_event_hub: RunEventHub | None,
    ) -> None:
        if not handler.async_rewake or result.decision is None:
            return
        if self._injection_manager is None:
            return
        if not event_input.instance_id:
            return
        if not self._injection_manager.is_active(event_input.run_id):
            return
        content = _async_rewake_content(result.decision)
        if not content:
            return
        _ = self._injection_manager.enqueue(
            event_input.run_id,
            event_input.instance_id,
            source=InjectionSource.SYSTEM,
            content=content,
        )
        await _publish_hook_event_async(
            run_event_hub=run_event_hub,
            event_input=event_input,
            event_type=RunEventType.HOOK_DEFERRED,
            payload={
                "hook_event": event_input.event_name.value,
                "hook_name": result.handler_name,
                "deferred_action": content,
            },
        )

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
                else (
                    handler.url
                    if handler.type == HookHandlerType.HTTP
                    else (
                        handler.prompt
                        if handler.type == HookHandlerType.PROMPT
                        else handler.role_id
                    )
                )
            )
            or handler.type.value
        )
        await _publish_hook_event_async(
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
            elif handler.type == HookHandlerType.HTTP:
                decision = await self._http_executor.execute(
                    handler=handler,
                    event_input=event_input,
                )
            elif handler.type == HookHandlerType.PROMPT:
                if self._prompt_executor is None:
                    raise RuntimeError("Prompt hooks are not configured")
                decision = await self._prompt_executor.execute(
                    handler=handler,
                    event_input=event_input,
                )
            else:
                if self._agent_executor is None:
                    raise RuntimeError("Agent hooks are not configured")
                decision = await self._agent_executor.execute(
                    handler=handler,
                    event_input=event_input,
                )
            decision = _normalize_decision_for_event(
                event_input.event_name,
                decision,
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
            await _publish_hook_event_async(
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
        except NonBlockingHttpHookError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            await _publish_hook_event_async(
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
                    "non_blocking": True,
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
            await _publish_hook_event_async(
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
            if handler.on_error == HookOnError.FAIL:
                raise
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
    elif event_name in {HookEventName.STOP, HookEventName.SUBAGENT_STOP} and any(
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


def _normalize_decision_for_event(
    event_name: HookEventName,
    decision: HookDecision,
) -> HookDecision:
    if event_name == HookEventName.PERMISSION_DENIED:
        return HookDecision(
            decision=HookDecisionType.OBSERVE,
            reason=decision.reason,
            additional_context=decision.additional_context,
            deferred_action=decision.deferred_action,
        )
    if event_name in {
        HookEventName.NOTIFICATION,
        HookEventName.SUBAGENT_START,
    }:
        return HookDecision(
            decision=HookDecisionType.OBSERVE,
            reason=decision.reason,
            additional_context=decision.additional_context,
        )
    if event_name in _OBSERVE_ONLY_EVENTS:
        return HookDecision(
            decision=HookDecisionType.OBSERVE,
            reason=decision.reason,
        )
    return decision


def _handler_dedup_key(
    handler: HookHandlerConfig,
) -> Optional[tuple[str, ...]]:
    common = (
        handler.type.value,
        handler.name.strip(),
        str(handler.if_rule or "").strip(),
        str(handler.timeout_seconds),
        handler.on_error.value,
    )
    if handler.type == HookHandlerType.COMMAND:
        command = str(handler.command or "").strip()
        if not command:
            return None
        return (
            *common,
            str(handler.shell or ""),
            command,
        )
    if handler.type == HookHandlerType.HTTP:
        url = str(handler.url or "").strip()
        if not url:
            return None
        headers = tuple(
            f"{key}:{value}" for key, value in sorted(handler.headers.items())
        )
        return (
            *common,
            url,
            ",".join(headers),
            ",".join(handler.allowed_env_vars),
        )
    return None


def _decision_conflicts(
    executions: tuple[HookExecutionResult, ...],
) -> tuple[str, ...]:
    decisions = [
        item.decision
        for item in executions
        if item.status == HookExecutionStatus.COMPLETED and item.decision is not None
    ]
    if len(decisions) <= 1:
        return ()
    conflicts: list[str] = []
    decision_values = {item.decision for item in decisions}
    control_values = decision_values & {
        HookDecisionType.DENY,
        HookDecisionType.ASK,
        HookDecisionType.RETRY,
        HookDecisionType.UPDATED_INPUT,
        HookDecisionType.SET_ENV,
        HookDecisionType.DEFER,
    }
    if len(control_values) > 1:
        conflicts.append(
            "conflicting_control_decisions:"
            + ",".join(sorted(item.value for item in control_values))
        )
    updated_input_count = sum(
        1
        for item in decisions
        if item.decision == HookDecisionType.UPDATED_INPUT
        and item.updated_input is not None
    )
    if updated_input_count > 1:
        conflicts.append("multiple_updated_input_decisions")
    deferred_count = sum(1 for item in decisions if item.deferred_action)
    if deferred_count > 1:
        conflicts.append("multiple_deferred_actions")
    return tuple(conflicts)


def _async_rewake_content(decision: HookDecision) -> str:
    deferred_action = decision.deferred_action.strip()
    context_text = "\n\n".join(
        item.strip() for item in decision.additional_context if item.strip()
    )
    return "\n\n".join(item for item in (context_text, deferred_action) if item).strip()


def _default_decision(event_name: HookEventName) -> HookDecisionType:
    if event_name in {
        HookEventName.POST_TOOL_USE,
        HookEventName.POST_TOOL_USE_FAILURE,
    }:
        return HookDecisionType.CONTINUE
    if (
        event_name in _OBSERVE_ONLY_EVENTS
        or event_name == HookEventName.PERMISSION_DENIED
    ):
        return HookDecisionType.OBSERVE
    return HookDecisionType.ALLOW


async def _publish_hook_event_async(
    *,
    run_event_hub: RunEventHub | None,
    event_input: HookEventInput,
    event_type: RunEventType,
    payload: dict[str, JsonValue],
) -> None:
    if run_event_hub is None:
        return
    await publish_run_event_async(
        run_event_hub,
        RunEvent(
            session_id=event_input.session_id,
            run_id=event_input.run_id,
            trace_id=event_input.trace_id,
            task_id=event_input.task_id,
            instance_id=event_input.instance_id,
            role_id=event_input.role_id,
            event_type=event_type,
            payload_json=dumps(payload, ensure_ascii=False),
        ),
    )
