from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from relay_teams.hooks.executors.command_executor import CommandHookExecutor
from relay_teams.hooks.executors.http_executor import (
    HttpHookExecutor,
    NonBlockingHttpHookError,
)
from relay_teams.hooks.executors.agent_executor import AgentHookExecutor
from relay_teams.hooks.executors.prompt_executor import PromptHookExecutor
from relay_teams.hooks.hook_event_models import (
    HookEventInput,
    InstructionsLoadedInput,
    NotificationInput,
    PermissionDeniedInput,
    PermissionRequestInput,
    PostCompactInput,
    PostToolUseFailureInput,
    PostToolUseInput,
    PreCompactInput,
    PreToolUseInput,
    SessionEndInput,
    SessionStartInput,
    StopFailureInput,
    StopInput,
    SubagentStartInput,
    SubagentStopInput,
    TaskCompletedInput,
    TaskCreatedInput,
    UserPromptSubmitInput,
)
from relay_teams.hooks.hook_loader import HookLoader
from relay_teams.hooks.hook_models import (
    HookDecision,
    HookDecisionType,
    HookEventName,
    HookExecutionResult,
    HookExecutionStatus,
    HookHandlerConfig,
    HookHandlerType,
    HookMatcherGroup,
    HookOnError,
    HookRuntimeSnapshot,
    HookShell,
    HooksConfig,
    ResolvedHookMatcherGroup,
    HookSourceInfo,
    HookSourceScope,
)
from relay_teams.hooks.hook_runtime_state import HookRuntimeState
from relay_teams.hooks.hook_service import (
    HookService,
    _decision_conflicts,
    _default_decision,
    _handler_dedup_key,
    _merge_decisions,
    _normalize_decision_for_event,
)
from relay_teams.media import UserPromptContent
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent


def test_merge_decisions_retries_subagent_stop() -> None:
    bundle = _merge_decisions(
        HookEventName.SUBAGENT_STOP,
        [
            HookExecutionResult(
                source=HookSourceInfo(
                    scope=HookSourceScope.USER,
                    path=Path("hooks.json"),
                ),
                event_name=HookEventName.SUBAGENT_STOP,
                handler_name="verifier",
                handler_type=HookHandlerType.COMMAND,
                status=HookExecutionStatus.COMPLETED,
                decision=HookDecision(
                    decision=HookDecisionType.RETRY,
                    reason="needs another pass",
                    additional_context=("fix the missing output",),
                ),
            )
        ],
    )

    assert bundle.decision == HookDecisionType.RETRY
    assert bundle.reason == "needs another pass"
    assert bundle.additional_context == ("fix the missing output",)


def test_default_decisions_for_observe_only_events() -> None:
    assert _default_decision(HookEventName.SESSION_END) == HookDecisionType.OBSERVE
    assert _default_decision(HookEventName.STOP_FAILURE) == HookDecisionType.OBSERVE
    assert _default_decision(HookEventName.SUBAGENT_START) == HookDecisionType.OBSERVE
    assert _default_decision(HookEventName.POST_COMPACT) == HookDecisionType.OBSERVE
    assert _default_decision(HookEventName.NOTIFICATION) == HookDecisionType.OBSERVE
    assert (
        _default_decision(HookEventName.INSTRUCTIONS_LOADED) == HookDecisionType.OBSERVE
    )


def _handler_config_for_type(handler_type: HookHandlerType) -> HookHandlerConfig:
    if handler_type == HookHandlerType.COMMAND:
        return HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            name="command-handler",
            command="echo ok",
        )
    if handler_type == HookHandlerType.HTTP:
        return HookHandlerConfig(
            type=HookHandlerType.HTTP,
            name="http-handler",
            url="https://hooks.example.test/events",
        )
    if handler_type == HookHandlerType.PROMPT:
        return HookHandlerConfig(
            type=HookHandlerType.PROMPT,
            name="prompt-handler",
            prompt="Return allow.",
        )
    return HookHandlerConfig(
        type=HookHandlerType.AGENT,
        name="agent-handler",
        prompt="Review the hook event and return allow.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_input", "handler_types"),
    [
        (
            SessionStartInput(
                event_name=HookEventName.SESSION_START,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
            (HookHandlerType.COMMAND,),
        ),
        (
            SessionEndInput(
                event_name=HookEventName.SESSION_END,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
            (HookHandlerType.COMMAND, HookHandlerType.HTTP),
        ),
        (
            UserPromptSubmitInput(
                event_name=HookEventName.USER_PROMPT_SUBMIT,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                user_prompt="implement hooks",
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            PreToolUseInput(
                event_name=HookEventName.PRE_TOOL_USE,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                tool_name="shell",
                tool_call_id="tool-1",
                tool_input={"command": "git status"},
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            PermissionRequestInput(
                event_name=HookEventName.PERMISSION_REQUEST,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                tool_name="shell",
                tool_call_id="tool-1",
                tool_input={"command": "git status"},
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            PermissionDeniedInput(
                event_name=HookEventName.PERMISSION_DENIED,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                tool_name="shell",
                tool_call_id="tool-1",
                tool_input={"command": "git status"},
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            PostToolUseInput(
                event_name=HookEventName.POST_TOOL_USE,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                tool_name="shell",
                tool_call_id="tool-1",
                tool_input={"command": "git status"},
                tool_result={"ok": True},
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            PostToolUseFailureInput(
                event_name=HookEventName.POST_TOOL_USE_FAILURE,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                tool_name="shell",
                tool_call_id="tool-1",
                tool_input={"command": "git status"},
                tool_error={"message": "failed"},
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            StopInput(
                event_name=HookEventName.STOP,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            StopFailureInput(
                event_name=HookEventName.STOP_FAILURE,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
            (HookHandlerType.COMMAND, HookHandlerType.HTTP),
        ),
        (
            SubagentStartInput(
                event_name=HookEventName.SUBAGENT_START,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                subagent_run_id="run-2",
                subagent_task_id="task-2",
                subagent_instance_id="instance-2",
                subagent_role_id="Explorer",
            ),
            (HookHandlerType.COMMAND, HookHandlerType.HTTP),
        ),
        (
            SubagentStopInput(
                event_name=HookEventName.SUBAGENT_STOP,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                subagent_run_id="run-2",
                subagent_task_id="task-2",
                subagent_instance_id="instance-2",
                subagent_role_id="Explorer",
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            TaskCreatedInput(
                event_name=HookEventName.TASK_CREATED,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                created_task_id="task-2",
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            TaskCompletedInput(
                event_name=HookEventName.TASK_COMPLETED,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                role_id="Engineer",
                completed_task_id="task-2",
            ),
            (
                HookHandlerType.COMMAND,
                HookHandlerType.HTTP,
                HookHandlerType.PROMPT,
                HookHandlerType.AGENT,
            ),
        ),
        (
            PreCompactInput(
                event_name=HookEventName.PRE_COMPACT,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                conversation_id="conversation-1",
            ),
            (HookHandlerType.COMMAND, HookHandlerType.HTTP),
        ),
        (
            PostCompactInput(
                event_name=HookEventName.POST_COMPACT,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                conversation_id="conversation-1",
            ),
            (HookHandlerType.COMMAND, HookHandlerType.HTTP),
        ),
        (
            NotificationInput(
                event_name=HookEventName.NOTIFICATION,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                notification_type="info",
                title="Build",
                body="done",
            ),
            (HookHandlerType.COMMAND, HookHandlerType.HTTP),
        ),
        (
            InstructionsLoadedInput(
                event_name=HookEventName.INSTRUCTIONS_LOADED,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
            (HookHandlerType.COMMAND, HookHandlerType.HTTP),
        ),
    ],
)
async def test_supported_events_dispatch_supported_handler_types(
    tmp_path: Path,
    event_input: HookEventInput,
    handler_types: tuple[HookHandlerType, ...],
) -> None:
    class RecordingCommandExecutor(CommandHookExecutor):
        def __init__(self) -> None:
            self.calls: list[HookEventName] = []

        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = handler
            self.calls.append(event_input.event_name)
            return HookDecision(decision=HookDecisionType.ALLOW)

    class RecordingHttpExecutor(HttpHookExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[HookEventName] = []

        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = handler
            self.calls.append(event_input.event_name)
            return HookDecision(decision=HookDecisionType.ALLOW)

    class RecordingPromptExecutor:
        def __init__(self) -> None:
            self.calls: list[HookEventName] = []

        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = handler
            self.calls.append(event_input.event_name)
            return HookDecision(decision=HookDecisionType.ALLOW)

    class RecordingAgentExecutor:
        def __init__(self) -> None:
            self.calls: list[HookEventName] = []

        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = handler
            self.calls.append(event_input.event_name)
            return HookDecision(decision=HookDecisionType.ALLOW)

    source = HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path / "hooks.json")
    command_executor = RecordingCommandExecutor()
    http_executor = RecordingHttpExecutor()
    prompt_executor = RecordingPromptExecutor()
    agent_executor = RecordingAgentExecutor()
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, command_executor),
        http_executor=cast(HttpHookExecutor, http_executor),
        prompt_executor=cast(PromptHookExecutor, prompt_executor),
        agent_executor=cast(AgentHookExecutor, agent_executor),
    )
    service.set_run_snapshot(
        event_input.run_id,
        HookRuntimeSnapshot(
            sources=(source,),
            hooks={
                event_input.event_name: (
                    ResolvedHookMatcherGroup(
                        source=source,
                        event_name=event_input.event_name,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=tuple(
                                _handler_config_for_type(handler_type)
                                for handler_type in handler_types
                            ),
                        ),
                    ),
                )
            },
        ),
    )
    event_hub = RunEventHub()
    queue = event_hub.subscribe(event_input.run_id)

    bundle = await service.execute(
        event_input=event_input,
        run_event_hub=event_hub,
    )

    assert len(bundle.executions) == len(handler_types)
    assert {execution.handler_type for execution in bundle.executions} == set(
        handler_types
    )
    assert len(command_executor.calls) == int(HookHandlerType.COMMAND in handler_types)
    assert len(http_executor.calls) == int(HookHandlerType.HTTP in handler_types)
    assert len(prompt_executor.calls) == int(HookHandlerType.PROMPT in handler_types)
    assert len(agent_executor.calls) == int(HookHandlerType.AGENT in handler_types)
    published_event_types = []
    while not queue.empty():
        published_event_types.append(queue.get_nowait().event_type)
    assert RunEventType.HOOK_MATCHED in published_event_types
    assert RunEventType.HOOK_COMPLETED in published_event_types
    assert RunEventType.HOOK_DECISION_APPLIED in published_event_types


@pytest.mark.asyncio
async def test_execute_publishes_hook_events_with_async_publisher(
    tmp_path: Path,
) -> None:
    class AsyncOnlyRunEventHub(RunEventHub):
        def __init__(self) -> None:
            super().__init__()
            self.events: list[RunEvent] = []

        def publish(self, event: RunEvent) -> int:
            _ = event
            raise AssertionError("HookService must not call sync publish in async flow")

        async def publish_async(self, event: RunEvent) -> int:
            self.events.append(event)
            return 1

    class AllowCommandExecutor(CommandHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = handler
            _ = event_input
            return HookDecision(decision=HookDecisionType.ALLOW)

    source = HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path / "hooks.json")
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=AllowCommandExecutor(),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(source,),
            hooks={
                HookEventName.TASK_CREATED: (
                    ResolvedHookMatcherGroup(
                        source=source,
                        event_name=HookEventName.TASK_CREATED,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    name="allow-task-created",
                                    command="echo ok",
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )
    event_hub = AsyncOnlyRunEventHub()

    _ = await service.execute(
        event_input=TaskCreatedInput(
            event_name=HookEventName.TASK_CREATED,
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            task_id="task-1",
            created_task_id="task-1",
            parent_task_id="root-task",
            title="Created task",
            objective="Create a child task",
        ),
        run_event_hub=event_hub,
    )

    assert [event.event_type for event in event_hub.events] == [
        RunEventType.HOOK_MATCHED,
        RunEventType.HOOK_STARTED,
        RunEventType.HOOK_COMPLETED,
        RunEventType.HOOK_DECISION_APPLIED,
    ]


def test_runtime_view_prefers_group_name_for_loaded_hook_name(tmp_path: Path) -> None:
    loader = HookLoader(app_config_dir=tmp_path, project_root=None)
    loader.save_user_config(
        HooksConfig(
            hooks={
                HookEventName.PRE_TOOL_USE: (
                    HookMatcherGroup(
                        name="Python write guard",
                        matcher="Write",
                        hooks=(
                            HookHandlerConfig(
                                type=HookHandlerType.COMMAND,
                                name="handler detail",
                                command="python lint.py",
                            ),
                        ),
                    ),
                )
            }
        )
    )
    service = HookService(
        loader=loader,
        runtime_state=HookRuntimeState(),
        command_executor=CommandHookExecutor(),
        http_executor=HttpHookExecutor(),
    )

    runtime_view = service.get_runtime_view()

    assert len(runtime_view.loaded_hooks) == 1
    assert runtime_view.loaded_hooks[0].name == "Python write guard"


def test_normalize_decision_for_notification_preserves_context_only() -> None:
    decision = _normalize_decision_for_event(
        HookEventName.NOTIFICATION,
        HookDecision(
            decision=HookDecisionType.DENY,
            reason="blocked",
            updated_input={"title": "rewrite"},
            additional_context=("ignored",),
            set_env={"A": "B"},
            deferred_action="ignored",
        ),
    )

    assert decision == HookDecision(
        decision=HookDecisionType.OBSERVE,
        reason="blocked",
        additional_context=("ignored",),
    )


def test_normalize_decision_for_observe_only_event_discards_control_fields() -> None:
    decision = _normalize_decision_for_event(
        HookEventName.INSTRUCTIONS_LOADED,
        HookDecision(
            decision=HookDecisionType.DENY,
            reason="blocked",
            updated_input={"title": "rewrite"},
            additional_context=("ignored",),
            set_env={"A": "B"},
            deferred_action="ignored",
        ),
    )

    assert decision == HookDecision(
        decision=HookDecisionType.OBSERVE,
        reason="blocked",
    )


def test_normalize_permission_denied_preserves_followup_context() -> None:
    decision = _normalize_decision_for_event(
        HookEventName.PERMISSION_DENIED,
        HookDecision(
            decision=HookDecisionType.DENY,
            reason="blocked",
            additional_context=("try a read-only command",),
            deferred_action="ask user for narrower permission",
        ),
    )

    assert decision == HookDecision(
        decision=HookDecisionType.OBSERVE,
        reason="blocked",
        additional_context=("try a read-only command",),
        deferred_action="ask user for narrower permission",
    )


def test_decision_conflicts_detects_incompatible_control_decisions() -> None:
    source = HookSourceInfo(scope=HookSourceScope.USER, path=Path("hooks.json"))
    conflicts = _decision_conflicts(
        (
            HookExecutionResult(
                source=source,
                event_name=HookEventName.PRE_TOOL_USE,
                handler_name="deny",
                handler_type=HookHandlerType.COMMAND,
                status=HookExecutionStatus.COMPLETED,
                decision=HookDecision(decision=HookDecisionType.DENY),
            ),
            HookExecutionResult(
                source=source,
                event_name=HookEventName.PRE_TOOL_USE,
                handler_name="rewrite",
                handler_type=HookHandlerType.COMMAND,
                status=HookExecutionStatus.COMPLETED,
                decision=HookDecision(
                    decision=HookDecisionType.UPDATED_INPUT,
                    updated_input={"command": "echo ok"},
                ),
            ),
        )
    )

    assert conflicts == ("conflicting_control_decisions:deny,updated_input",)


def test_decision_conflicts_detects_duplicate_rewrites_and_deferred_actions() -> None:
    source = HookSourceInfo(scope=HookSourceScope.USER, path=Path("hooks.json"))
    conflicts = _decision_conflicts(
        (
            HookExecutionResult(
                source=source,
                event_name=HookEventName.PRE_TOOL_USE,
                handler_name="rewrite-1",
                handler_type=HookHandlerType.COMMAND,
                status=HookExecutionStatus.COMPLETED,
                decision=HookDecision(
                    decision=HookDecisionType.UPDATED_INPUT,
                    updated_input={"command": "echo one"},
                    deferred_action="follow up one",
                ),
            ),
            HookExecutionResult(
                source=source,
                event_name=HookEventName.PRE_TOOL_USE,
                handler_name="rewrite-2",
                handler_type=HookHandlerType.COMMAND,
                status=HookExecutionStatus.COMPLETED,
                decision=HookDecision(
                    decision=HookDecisionType.UPDATED_INPUT,
                    updated_input={"command": "echo two"},
                    deferred_action="follow up two",
                ),
            ),
        )
    )

    assert "multiple_updated_input_decisions" in conflicts
    assert "multiple_deferred_actions" in conflicts


def test_handler_dedup_key_covers_command_http_and_other_handlers() -> None:
    command_key = _handler_dedup_key(
        HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            command="python hook.py",
        )
    )
    assert command_key == (
        "command",
        "",
        "",
        "5.0",
        "ignore",
        "",
        "python hook.py",
    )
    assert _handler_dedup_key(
        HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            command="python hook.py",
            shell=HookShell.POWERSHELL,
        )
    ) == (
        "command",
        "",
        "",
        "5.0",
        "ignore",
        str(HookShell.POWERSHELL),
        "python hook.py",
    )
    assert (
        _handler_dedup_key(
            HookHandlerConfig(
                type=HookHandlerType.COMMAND,
                if_rule="shell(git *)",
                command="python hook.py",
            )
        )
        != command_key
    )
    assert _handler_dedup_key(
        HookHandlerConfig(
            type=HookHandlerType.HTTP,
            url="https://hook.test/",
        )
    ) == ("http", "", "", "5.0", "ignore", "https://hook.test/", "", "")
    assert (
        _handler_dedup_key(
            HookHandlerConfig(
                type=HookHandlerType.PROMPT,
                prompt="return allow",
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_async_hook_does_not_control_current_decision(tmp_path: Path) -> None:
    completed = asyncio.Event()

    class AsyncDenyCommandExecutor(CommandHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            await asyncio.sleep(0)
            completed.set()
            return HookDecision(decision=HookDecisionType.DENY)

    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, AsyncDenyCommandExecutor()),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path),),
            hooks={
                HookEventName.SESSION_START: (
                    ResolvedHookMatcherGroup(
                        source=HookSourceInfo(
                            scope=HookSourceScope.USER,
                            path=tmp_path / "hooks.json",
                        ),
                        event_name=HookEventName.SESSION_START,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    command="ignored",
                                    run_async=True,
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )

    bundle = await service.execute(
        event_input=SessionStartInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
        run_event_hub=None,
    )

    assert bundle.decision == HookDecisionType.ALLOW
    assert bundle.executions == ()
    await asyncio.wait_for(completed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_async_hook_tasks_are_retained_until_done(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingCommandExecutor(CommandHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = (handler, event_input)
            started.set()
            await release.wait()
            return HookDecision(decision=HookDecisionType.ALLOW)

    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, BlockingCommandExecutor()),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path),),
            hooks={
                HookEventName.SESSION_START: (
                    ResolvedHookMatcherGroup(
                        source=HookSourceInfo(
                            scope=HookSourceScope.USER,
                            path=tmp_path / "hooks.json",
                        ),
                        event_name=HookEventName.SESSION_START,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    command="sleep",
                                    run_async=True,
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )

    _ = await service.execute(
        event_input=SessionStartInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
        run_event_hub=None,
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    assert len(service._background_tasks) == 1
    release.set()
    pending = tuple(service._background_tasks)
    if pending:
        await asyncio.gather(*pending)
    assert service._background_tasks == set()


@pytest.mark.asyncio
async def test_on_error_fail_reraises_hook_failure(tmp_path: Path) -> None:
    class FailingCommandExecutor(CommandHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            raise RuntimeError("boom")

    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, FailingCommandExecutor()),
        http_executor=HttpHookExecutor(),
    )

    with pytest.raises(RuntimeError, match="boom"):
        _ = await service._execute_handler(
            event_input=SessionStartInput(
                event_name=HookEventName.SESSION_START,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
            handler=HookHandlerConfig(
                type=HookHandlerType.COMMAND,
                command="ignored",
                on_error=HookOnError.FAIL,
            ),
            source=HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path),
            run_event_hub=None,
        )


@pytest.mark.asyncio
async def test_on_error_fail_stops_later_sync_handlers(tmp_path: Path) -> None:
    class OrderedCommandExecutor(CommandHookExecutor):
        def __init__(self) -> None:
            self.commands: list[str] = []

        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = event_input
            command = str(handler.command or "")
            self.commands.append(command)
            if command == "fail":
                raise RuntimeError("stop chain")
            return HookDecision(decision=HookDecisionType.ALLOW)

    source = HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path / "hooks.json")
    executor = OrderedCommandExecutor()
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, executor),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(source,),
            hooks={
                HookEventName.SESSION_START: (
                    ResolvedHookMatcherGroup(
                        source=source,
                        event_name=HookEventName.SESSION_START,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    command="fail",
                                    on_error=HookOnError.FAIL,
                                ),
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    command="mutate",
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )

    with pytest.raises(RuntimeError, match="stop chain"):
        _ = await service.execute(
            event_input=SessionStartInput(
                event_name=HookEventName.SESSION_START,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
            run_event_hub=None,
        )

    assert executor.commands == ["fail"]


@pytest.mark.asyncio
async def test_execute_publishes_hook_conflict_event(tmp_path: Path) -> None:
    class ConflictingCommandExecutor(CommandHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            if handler.name == "deny":
                return HookDecision(decision=HookDecisionType.DENY)
            return HookDecision(
                decision=HookDecisionType.UPDATED_INPUT,
                updated_input={"value": "rewritten"},
            )

    source = HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path / "hooks.json")
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, ConflictingCommandExecutor()),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(source,),
            hooks={
                HookEventName.SESSION_START: (
                    ResolvedHookMatcherGroup(
                        source=source,
                        event_name=HookEventName.SESSION_START,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    name="deny",
                                    command="deny-command",
                                ),
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    name="rewrite",
                                    command="rewrite-command",
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )
    event_hub = RunEventHub()
    queue = event_hub.subscribe("run-1")

    _ = await service.execute(
        event_input=SessionStartInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
        run_event_hub=event_hub,
    )
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert any(event.event_type == RunEventType.HOOK_CONFLICT for event in events)
    assert any(event.event_type == RunEventType.HOOK_STARTED for event in events)


@pytest.mark.asyncio
async def test_execute_runs_sync_handlers_concurrently(tmp_path: Path) -> None:
    class BarrierCommandExecutor(CommandHookExecutor):
        def __init__(self) -> None:
            self.started = 0
            self.both_started = asyncio.Event()

        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = (handler, event_input)
            self.started += 1
            if self.started == 2:
                self.both_started.set()
            await self.both_started.wait()
            return HookDecision(decision=HookDecisionType.ALLOW)

    source = HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path / "hooks.json")
    executor = BarrierCommandExecutor()
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, executor),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(source,),
            hooks={
                HookEventName.SESSION_START: (
                    ResolvedHookMatcherGroup(
                        source=source,
                        event_name=HookEventName.SESSION_START,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    name="one",
                                    command="one",
                                ),
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    name="two",
                                    command="two",
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )

    bundle = await asyncio.wait_for(
        service.execute(
            event_input=SessionStartInput(
                event_name=HookEventName.SESSION_START,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
            run_event_hub=None,
        ),
        timeout=1,
    )

    assert len(bundle.executions) == 2


@pytest.mark.asyncio
async def test_execute_filters_handlers_by_tool_if_rule(tmp_path: Path) -> None:
    class RecordingCommandExecutor(CommandHookExecutor):
        def __init__(self) -> None:
            self.commands: list[str] = []

        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = event_input
            self.commands.append(str(handler.command or ""))
            return HookDecision(decision=HookDecisionType.ALLOW)

    source = HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path / "hooks.json")
    executor = RecordingCommandExecutor()
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, executor),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(source,),
            hooks={
                HookEventName.PRE_TOOL_USE: (
                    ResolvedHookMatcherGroup(
                        source=source,
                        event_name=HookEventName.PRE_TOOL_USE,
                        group=HookMatcherGroup(
                            matcher="shell",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    command="git-policy",
                                    if_rule="Bash(git *)",
                                ),
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    command="npm-policy",
                                    if_rule="Bash(npm *)",
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )

    bundle = await service.execute(
        event_input=PreToolUseInput(
            event_name=HookEventName.PRE_TOOL_USE,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="shell",
            tool_call_id="call-1",
            tool_input={"command": "git status --short"},
        ),
        run_event_hub=None,
    )

    assert bundle.decision == HookDecisionType.ALLOW
    assert executor.commands == ["git-policy"]


@pytest.mark.asyncio
async def test_execute_deduplicates_identical_command_handlers(tmp_path: Path) -> None:
    class CountingCommandExecutor(CommandHookExecutor):
        def __init__(self) -> None:
            self.calls = 0

        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = (handler, event_input)
            self.calls += 1
            return HookDecision(decision=HookDecisionType.ALLOW)

    source = HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path / "hooks.json")
    executor = CountingCommandExecutor()
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, executor),
        http_executor=HttpHookExecutor(),
    )
    duplicate = HookHandlerConfig(
        type=HookHandlerType.COMMAND,
        command="same-command",
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(source,),
            hooks={
                HookEventName.SESSION_START: (
                    ResolvedHookMatcherGroup(
                        source=source,
                        event_name=HookEventName.SESSION_START,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(duplicate, duplicate),
                        ),
                    ),
                )
            },
        ),
    )

    bundle = await service.execute(
        event_input=SessionStartInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
        run_event_hub=None,
    )

    assert executor.calls == 1
    assert len(bundle.executions) == 1


@pytest.mark.asyncio
async def test_execute_normalizes_observe_only_control_decision(tmp_path: Path) -> None:
    class DenyingCommandExecutor(CommandHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = (handler, event_input)
            return HookDecision(decision=HookDecisionType.DENY, reason="nope")

    source = HookSourceInfo(scope=HookSourceScope.USER, path=tmp_path / "hooks.json")
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, DenyingCommandExecutor()),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            sources=(source,),
            hooks={
                HookEventName.SESSION_END: (
                    ResolvedHookMatcherGroup(
                        source=source,
                        event_name=HookEventName.SESSION_END,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    name="deny",
                                    command="ignored",
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )
    event_hub = RunEventHub()
    queue = event_hub.subscribe("run-1")

    bundle = await service.execute(
        event_input=SessionEndInput(
            event_name=HookEventName.SESSION_END,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
        run_event_hub=event_hub,
    )
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert bundle.decision == HookDecisionType.OBSERVE
    completed_payloads = [
        event.payload_json
        for event in events
        if event.event_type == RunEventType.HOOK_COMPLETED
    ]
    assert len(completed_payloads) == 1
    assert '"decision": "observe"' in completed_payloads[0]


@pytest.mark.asyncio
async def test_async_rewake_enqueues_followup_context(tmp_path: Path) -> None:
    completed = asyncio.Event()

    class RewakeCommandExecutor(CommandHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            completed.set()
            return HookDecision(
                decision=HookDecisionType.ADDITIONAL_CONTEXT,
                additional_context=("async context",),
                deferred_action="async deferred",
            )

    class FakeInjectionManager:
        def __init__(self) -> None:
            self.items: list[tuple[str, str, InjectionSource, UserPromptContent]] = []

        def is_active(self, run_id: str) -> bool:
            return run_id == "run-1"

        def enqueue(
            self,
            run_id: str,
            recipient_instance_id: str,
            source: InjectionSource,
            content: UserPromptContent,
            sender_instance_id: str | None = None,
            sender_role_id: str | None = None,
        ) -> object:
            _ = sender_instance_id
            _ = sender_role_id
            self.items.append((run_id, recipient_instance_id, source, content))
            return object()

    injection_manager = FakeInjectionManager()
    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, RewakeCommandExecutor()),
        http_executor=HttpHookExecutor(),
    )
    service.set_injection_manager(injection_manager)
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            hooks={
                HookEventName.SESSION_START: (
                    ResolvedHookMatcherGroup(
                        source=HookSourceInfo(
                            scope=HookSourceScope.USER,
                            path=tmp_path / "hooks.json",
                        ),
                        event_name=HookEventName.SESSION_START,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    command="ignored",
                                    run_async=True,
                                    async_rewake=True,
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )

    _ = await service.execute(
        event_input=SessionStartInput(
            event_name=HookEventName.SESSION_START,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            instance_id="instance-1",
        ),
        run_event_hub=None,
    )
    await asyncio.wait_for(completed.wait(), timeout=1)
    await asyncio.sleep(0)

    assert injection_manager.items == [
        (
            "run-1",
            "instance-1",
            InjectionSource.SYSTEM,
            "async context\n\nasync deferred",
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_input",
    [
        PermissionDeniedInput(
            event_name=HookEventName.PERMISSION_DENIED,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="shell",
            tool_call_id="tool-1",
            tool_input={"command": "git status"},
            denial_source="hook",
        ),
        PermissionRequestInput(
            event_name=HookEventName.PERMISSION_REQUEST,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="shell",
            tool_call_id="tool-1",
            tool_input={"command": "git status"},
        ),
        PostToolUseInput(
            event_name=HookEventName.POST_TOOL_USE,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="shell",
            tool_call_id="tool-1",
            tool_input={"command": "git status"},
            tool_result={"output": "ok"},
        ),
        PostToolUseFailureInput(
            event_name=HookEventName.POST_TOOL_USE_FAILURE,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="shell",
            tool_call_id="tool-1",
            tool_input={"command": "git status"},
            tool_error={"message": "failed"},
        ),
    ],
)
async def test_tool_event_variants_use_tool_name_for_matching(
    tmp_path: Path,
    event_input: HookEventInput,
) -> None:
    class AllowCommandExecutor(CommandHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = handler
            _ = event_input
            return HookDecision(decision=HookDecisionType.ALLOW)

    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=cast(CommandHookExecutor, AllowCommandExecutor()),
        http_executor=HttpHookExecutor(),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            hooks={
                event_input.event_name: (
                    ResolvedHookMatcherGroup(
                        source=HookSourceInfo(
                            scope=HookSourceScope.USER,
                            path=tmp_path / "hooks.json",
                        ),
                        event_name=event_input.event_name,
                        group=HookMatcherGroup(
                            matcher="shell",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.COMMAND,
                                    command="ignored",
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )

    bundle = await service.execute(event_input=event_input, run_event_hub=None)

    assert len(bundle.executions) == 1


@pytest.mark.asyncio
async def test_http_non_blocking_failure_is_returned_as_failed_execution(
    tmp_path: Path,
) -> None:
    class FailingHttpExecutor(HttpHookExecutor):
        async def execute(
            self,
            *,
            handler: HookHandlerConfig,
            event_input: HookEventInput,
        ) -> HookDecision:
            _ = handler
            _ = event_input
            raise NonBlockingHttpHookError("status 500")

    service = HookService(
        loader=HookLoader(app_config_dir=tmp_path, project_root=None),
        runtime_state=HookRuntimeState(),
        command_executor=CommandHookExecutor(),
        http_executor=cast(HttpHookExecutor, FailingHttpExecutor()),
    )
    service.set_run_snapshot(
        "run-1",
        HookRuntimeSnapshot(
            hooks={
                HookEventName.SESSION_END: (
                    ResolvedHookMatcherGroup(
                        source=HookSourceInfo(
                            scope=HookSourceScope.USER,
                            path=tmp_path / "hooks.json",
                        ),
                        event_name=HookEventName.SESSION_END,
                        group=HookMatcherGroup(
                            matcher="*",
                            hooks=(
                                HookHandlerConfig(
                                    type=HookHandlerType.HTTP,
                                    url="https://hook.test/",
                                ),
                            ),
                        ),
                    ),
                )
            },
        ),
    )

    bundle = await service.execute(
        event_input=SessionEndInput(
            event_name=HookEventName.SESSION_END,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
        run_event_hub=None,
    )

    assert bundle.executions[0].status == HookExecutionStatus.FAILED
    assert bundle.executions[0].error == "status 500"
