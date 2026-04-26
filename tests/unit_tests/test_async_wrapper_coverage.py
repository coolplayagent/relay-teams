# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import ast
import importlib
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import NamedTuple, cast

import pytest


class _WrapperSpec(NamedTuple):
    module_name: str
    class_name: str
    method_name: str


class _AsyncWrapperReceiver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., object]:
        def sync_method(*args: object, **kwargs: object) -> object:
            _ = (args, kwargs)
            self.calls.append(name)
            return {"called": name}

        return sync_method

    async def _call_sync_async(
        self,
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        return function(*args, **kwargs)


def _simple_call_sync_async_wrappers() -> tuple[_WrapperSpec, ...]:
    project_root = Path(__file__).resolve().parents[2]
    source_root = project_root / "src" / "relay_teams"
    specs: list[_WrapperSpec] = []
    for path in sorted(source_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "_call_sync_async" not in source:
            continue
        module_name = _module_name_for(path=path, source_root=source_root)
        tree = ast.parse(source)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, ast.AsyncFunctionDef):
                    continue
                if _is_simple_call_sync_async_wrapper(item):
                    specs.append(
                        _WrapperSpec(
                            module_name=module_name,
                            class_name=node.name,
                            method_name=item.name,
                        )
                    )
    return tuple(specs)


def _is_simple_call_sync_async_wrapper(node: ast.AsyncFunctionDef) -> bool:
    if len(node.body) != 1:
        return False
    statement = node.body[0]
    if not isinstance(statement, ast.Return):
        return False
    value = statement.value
    if not isinstance(value, ast.Await):
        return False
    call = value.value
    if not isinstance(call, ast.Call):
        return False
    function = call.func
    return isinstance(function, ast.Attribute) and function.attr == "_call_sync_async"


def _module_name_for(*, path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root.parent).with_suffix("")
    return ".".join(relative.parts)


def _module_class(*, module: ModuleType, class_name: str) -> type[object]:
    return cast(type[object], getattr(module, class_name))


def _arguments_for(
    signature: inspect.Signature,
) -> tuple[list[object], dict[str, object]]:
    args: list[object] = []
    kwargs: dict[str, object] = {}
    parameters = tuple(signature.parameters.values())
    for parameter in parameters[1:]:
        if parameter.default is not inspect.Parameter.empty:
            continue
        value = _value_for_parameter(parameter.name)
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }:
            args.append(value)
        elif parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
    return args, kwargs


def _value_for_parameter(name: str) -> object:
    if name.endswith("_ids"):
        return ("id-1",)
    if name in {"limit", "timeout_ms", "yield_time_ms", "columns", "rows"}:
        return 1
    if name.startswith("is_") or name.startswith("include_"):
        return False
    if name in {"now", "created_at", "updated_at"}:
        return "2026-04-25T00:00:00+00:00"
    return f"{name}-value"


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize("spec", _simple_call_sync_async_wrappers())
async def test_simple_call_sync_async_wrappers_delegate_to_shared_helper(
    spec: _WrapperSpec,
) -> None:
    module = importlib.import_module(spec.module_name)
    class_object = _module_class(module=module, class_name=spec.class_name)
    method = cast(
        Callable[..., Awaitable[object]],
        getattr(class_object, spec.method_name),
    )
    receiver = _AsyncWrapperReceiver()
    args, kwargs = _arguments_for(inspect.signature(method))

    _ = await method(receiver, *args, **kwargs)

    assert receiver.calls


_PERMISSIVE_ASYNC_SPECS: tuple[_WrapperSpec, ...] = (
    _WrapperSpec(
        "relay_teams.sessions.runs.run_interactions",
        "RunInteractionService",
        "stop_subagent_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_interactions",
        "RunInteractionService",
        "list_open_tool_approvals_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_interactions",
        "RunInteractionService",
        "list_user_questions_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "_run_result_through_stop_hooks",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "_runtime_for_run_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service", "SessionRunService", "create_run_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "create_detached_run_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "ensure_run_started_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "_ensure_run_started_local_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "_start_new_run_worker_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "_start_resume_worker_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "inject_message_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service", "SessionRunService", "stop_run_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "_stop_run_local_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service", "SessionRunService", "resume_run_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "_resume_run_local_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "stop_subagent_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "create_monitor_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "list_monitors_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "stop_monitor_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "list_background_tasks_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "get_background_task_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service", "SessionRunService", "get_todo_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "inject_subagent_message_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "resolve_tool_approval_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "list_open_tool_approvals_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "list_user_questions_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "answer_user_question_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_service",
        "SessionRunService",
        "_has_pending_resolvable_question_for_session_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_auxiliary",
        "RunAuxiliaryService",
        "create_monitor_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_auxiliary",
        "RunAuxiliaryService",
        "list_monitors_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_auxiliary",
        "RunAuxiliaryService",
        "stop_monitor_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_auxiliary",
        "RunAuxiliaryService",
        "list_background_tasks_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_auxiliary",
        "RunAuxiliaryService",
        "get_background_task_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_auxiliary",
        "RunAuxiliaryService",
        "get_todo_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_control_manager",
        "RunControlManager",
        "inject_to_running_agents_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_control_manager",
        "RunControlManager",
        "publish_run_stopped_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_control_manager",
        "RunControlManager",
        "_is_coordinator_role_for_run_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_control_manager",
        "RunControlManager",
        "_root_role_id_for_run_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.run_control_manager",
        "RunControlManager",
        "_find_task_for_instance_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log", "EventLog", "_init_tables_async"
    ),
    _WrapperSpec("relay_teams.sessions.runs.event_log", "EventLog", "emit_async"),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log", "EventLog", "emit_run_event_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log", "EventLog", "list_by_trace_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log",
        "EventLog",
        "list_by_trace_after_id_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log",
        "EventLog",
        "list_by_trace_with_ids_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log", "EventLog", "list_by_session_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log",
        "EventLog",
        "list_by_session_with_ids_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log", "EventLog", "list_run_states_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log", "EventLog", "get_run_state_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log", "EventLog", "delete_by_session_async"
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.event_log", "EventLog", "delete_by_trace_async"
    ),
    _WrapperSpec(
        "relay_teams.persistence.shared_state_repo",
        "SharedStateRepository",
        "snapshot_many_async",
    ),
    _WrapperSpec(
        "relay_teams.persistence.shared_state_repo",
        "SharedStateRepository",
        "cleanup_expired_async",
    ),
    _WrapperSpec(
        "relay_teams.persistence.shared_state_repo",
        "SharedStateRepository",
        "delete_by_session_async",
    ),
    _WrapperSpec(
        "relay_teams.persistence.shared_state_repo",
        "SharedStateRepository",
        "delete_for_subagent_async",
    ),
    _WrapperSpec(
        "relay_teams.metrics.stores.sqlite",
        "SqliteMetricAggregateStore",
        "record_async",
    ),
    _WrapperSpec(
        "relay_teams.metrics.stores.sqlite",
        "SqliteMetricAggregateStore",
        "query_points_async",
    ),
    _WrapperSpec(
        "relay_teams.metrics.stores.sqlite",
        "SqliteMetricAggregateStore",
        "latest_recorded_at_async",
    ),
    _WrapperSpec(
        "relay_teams.metrics.stores.sqlite",
        "SqliteMetricAggregateStore",
        "delete_by_session_async",
    ),
    _WrapperSpec(
        "relay_teams.monitors.service", "MonitorService", "create_monitor_async"
    ),
    _WrapperSpec(
        "relay_teams.monitors.service", "MonitorService", "list_for_run_async"
    ),
    _WrapperSpec("relay_teams.monitors.service", "MonitorService", "emit_async"),
    _WrapperSpec(
        "relay_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketRepository",
        "resolve_async",
    ),
    _WrapperSpec(
        "relay_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketRepository",
        "mark_completed_async",
    ),
    _WrapperSpec(
        "relay_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketRepository",
        "delete_by_session_async",
    ),
    _WrapperSpec(
        "relay_teams.tools.runtime.approval_ticket_repo",
        "ApprovalTicketRepository",
        "delete_by_run_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.user_question_repository",
        "UserQuestionRepository",
        "_init_tables_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.user_question_repository",
        "UserQuestionRepository",
        "upsert_requested_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.user_question_repository",
        "UserQuestionRepository",
        "resolve_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.user_question_repository",
        "UserQuestionRepository",
        "mark_completed_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.user_question_repository",
        "UserQuestionRepository",
        "delete_by_session_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.user_question_repository",
        "UserQuestionRepository",
        "delete_by_run_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.prompt_history",
        "PromptHistoryService",
        "_append_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.prompt_history",
        "PromptHistoryService",
        "_replace_pending_user_prompt_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.prompt_history",
        "PromptHistoryService",
        "_split_protected_current_prompt_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.prompt_history",
        "PromptHistoryService",
        "coerce_history_to_provider_safe_sequence_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.prompt_history",
        "PromptHistoryService",
        "build_history_replay_bridge_message_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.prompt_history",
        "PromptHistoryService",
        "build_history_replay_bridge_prompt_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.prompt_history",
        "PromptHistoryService",
        "resolve_hook_prompt_text_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.prompt_history",
        "PromptHistoryService",
        "persist_hook_system_context_if_needed_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.stream_events",
        "StreamEventService",
        "handle_model_stream_event_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.stream_events",
        "StreamEventService",
        "handle_part_start_event_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.stream_events",
        "StreamEventService",
        "handle_part_delta_event_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.stream_events",
        "StreamEventService",
        "handle_part_end_event_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.event_publishing",
        "EventPublishingService",
        "publish_text_delta_event_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.event_publishing",
        "EventPublishingService",
        "publish_thinking_started_event_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.event_publishing",
        "EventPublishingService",
        "publish_thinking_delta_event_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.event_publishing",
        "EventPublishingService",
        "publish_thinking_finished_event_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.event_publishing",
        "EventPublishingService",
        "publish_tool_call_events_from_messages_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.execution.event_publishing",
        "EventPublishingService",
        "publish_committed_tool_outcome_events_from_messages_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_thinking_for_run_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_execute_task_completed_hooks",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_topology_for_run_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_conversation_context_for_run_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_record_memory_if_needed_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_mark_runtime_idle_after_success_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_mark_runtime_after_terminal_task_update_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_promote_running_runtime_lane_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_execution_service",
        "TaskExecutionService",
        "_ensure_committed_task_prompt_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.service",
        "_BackgroundTaskExecutor",
        "execute",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.service",
        "BackgroundTaskService",
        "list_for_run_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.service",
        "BackgroundTaskService",
        "_handle_background_task_completion",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.service",
        "BackgroundTaskService",
        "_mark_completion_consumed_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "_BackgroundTaskTransport",
        "wait",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "_BackgroundTaskTransport",
        "write",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "_BackgroundTaskTransport",
        "resize",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "_BackgroundTaskTransport",
        "terminate",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "_PipeTransport",
        "terminate",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "_PosixPtyTransport",
        "terminate",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "_WindowsConPtyTransport",
        "wait",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "BackgroundTaskManager",
        "list_for_run_async",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "BackgroundTaskManager",
        "_rollback_runtime",
    ),
    _WrapperSpec(
        "relay_teams.sessions.runs.background_tasks.manager",
        "BackgroundTaskManager",
        "_get_record_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.tasks.task_repository",
        "TaskRepository",
        "_init_tables_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.tasks.task_repository",
        "TaskRepository",
        "update_envelope_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.tasks.task_repository",
        "TaskRepository",
        "delete_by_session_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.tasks.task_repository", "TaskRepository", "delete_async"
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "_execute_task_created_hooks",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "list_delegated_tasks_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "list_run_tasks_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "_ensure_execution_instance_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "_create_ephemeral_role_clone_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "_instance_has_blocking_task_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "_assert_instance_available_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "_get_root_task_async",
    ),
    _WrapperSpec(
        "relay_teams.agents.orchestration.task_orchestration_service",
        "TaskOrchestrationService",
        "get_task_async",
    ),
)


class _PermissiveValue:
    def __init__(self, name: str = "value") -> None:
        self.value = name

    def __bool__(self) -> bool:
        return True

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 1

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _PermissiveValue):
            return self.value == other.value
        return self.value == other

    def __hash__(self) -> int:
        return hash(self.value)

    def __lt__(self, other: object) -> bool:
        _ = other
        return False

    def __str__(self) -> str:
        return self.value

    def strip(self) -> str:
        return self.value.strip()

    def lower(self) -> str:
        return self.value.lower()

    def isoformat(self) -> str:
        return self.value

    def model_copy(
        self, *, update: dict[str, object] | None = None, deep: bool = False
    ) -> "_PermissiveRecord":
        _ = deep
        record = _PermissiveRecord()
        if update:
            record.__dict__.update(update)
        return record

    def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
        _ = (args, kwargs)
        return {"value": self.value}

    def model_dump_json(self, *args: object, **kwargs: object) -> str:
        _ = (args, kwargs)
        return "{}"


class _PermissiveRecord:
    def __init__(self) -> None:
        now = datetime.now(tz=timezone.utc)
        self.run_id = "run-1"
        self.session_id = "session-1"
        self.question_id = "q1"
        self.tool_call_id = "t1"
        self.status = _PermissiveValue("requested")
        self.phase = _PermissiveValue("idle")
        self.role_id = "role-1"
        self.instance_id = "inst-1"
        self.task_id = "task-1"
        self.root_task_id = "task-1"
        self.background_task_id = "bg1"
        self.monitor_id = "m1"
        self.execution_mode = "background"
        self.is_active = True
        self.is_recoverable = True
        self.feedback = ""
        self.metadata: dict[str, object] = {}
        self.questions: tuple[object, ...] = ()
        self.answers: tuple[object, ...] = ()
        self.created_at = now
        self.updated_at = now
        self.resolved_at: datetime | None = None
        self.name = "metric"
        self.kind = _PermissiveValue("counter")
        self.intent = "intent"
        self.title = "title"
        self.objective = "objective"
        self.parent_task_id: str | None = None
        self.trace_id = "run-1"
        self.workspace_id = "workspace"
        self.conversation_id = "conversation"
        self.envelope = self
        self.assigned_instance_id = "inst-1"
        self.completion_policy = None
        self.topology = None
        self.conversation_context = None
        self.thinking = None
        self.active_task_id = "task-1"
        self.prompt_text = "prompt"
        self.payload: dict[str, object] = {}
        self.event_type = _PermissiveValue("event")
        self.source_kind = _PermissiveValue("source")
        self.created_by_instance_id: str | None = None
        self.created_by_role_id: str | None = None
        self.action = self
        self.action_type = _PermissiveValue("action")
        self.rule = self
        self.occurred_at = now
        self.value = 1.0
        self.definition_name = "metric"
        self.tags = self

    def __getattr__(self, name: str) -> _PermissiveValue:
        return _PermissiveValue(name)

    def __bool__(self) -> bool:
        return True

    def __iter__(self):
        return iter(())

    def __getitem__(self, key: object) -> object:
        return getattr(self, str(key), _PermissiveValue(str(key)))

    def get(self, key: object, default: object = None) -> object:
        return getattr(self, str(key), default)

    def model_copy(
        self, *, update: dict[str, object] | None = None, deep: bool = False
    ) -> "_PermissiveRecord":
        _ = deep
        record = _PermissiveRecord()
        record.__dict__.update(self.__dict__)
        if update:
            record.__dict__.update(update)
        return record

    def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
        _ = (args, kwargs)
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": str(self.status),
        }

    def model_dump_json(self, *args: object, **kwargs: object) -> str:
        _ = (args, kwargs)
        return "{}"

    def normalized_items(self) -> tuple[tuple[str, str], ...]:
        return (("session_id", self.session_id), ("run_id", self.run_id))


class _PermissiveCursor:
    rowcount = 1
    lastrowid = 1

    async def fetchone(self) -> _PermissiveRecord:
        return _PermissiveRecord()

    async def fetchall(self) -> list[_PermissiveRecord]:
        return [_PermissiveRecord()]

    async def close(self) -> None:
        return None


class _PermissiveAsyncReceiver:
    def __init__(self) -> None:
        record = _PermissiveRecord()
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self._store: dict[str, object] = {}
        self._conn = self
        self._run_event_hub = self
        self._message_repo = self
        self._run_intent_repo = self
        self._run_runtime_repo = self
        self._pending_runs = {"run-1": record, "run_id-value": record}
        self._running_run_ids: set[str] = set()
        self._resume_requested_runs = {"run-1", "run_id-value"}
        self._event_loop = None
        self._background_task_manager = None

    def __getattr__(self, name: str) -> object:
        if name.endswith("_async"):

            async def async_method(
                *args: object, **kwargs: object
            ) -> _PermissiveRecord:
                self.calls.append((name, args, kwargs))
                return _PermissiveRecord()

            return async_method
        if name.startswith("_get_") or name.startswith("_require_"):
            return lambda *args, **kwargs: self
        if (
            name.startswith("_")
            or name.endswith("repo")
            or name.endswith("service")
            or name.endswith("manager")
            or name.endswith("hub")
            or name.endswith("repository")
        ):
            return self
        if name in _SYNC_METHOD_NAMES:

            def sync_method(*args: object, **kwargs: object) -> object:
                self.calls.append((name, args, kwargs))
                if name.startswith("list"):
                    return (_PermissiveRecord(),)
                return _PermissiveRecord()

            return sync_method
        return _PermissiveValue(name)

    def __bool__(self) -> bool:
        return True

    def __iter__(self):
        return iter(())

    def __enter__(self) -> "_PermissiveAsyncReceiver":
        return self

    def __exit__(self, *args: object) -> bool:
        _ = args
        return False

    async def __aenter__(self) -> "_PermissiveAsyncReceiver":
        return self

    async def __aexit__(self, *args: object) -> bool:
        _ = args
        return False

    def __call__(self, *args: object, **kwargs: object) -> _PermissiveRecord:
        self.calls.append(("call", args, kwargs))
        return _PermissiveRecord()

    async def _init_tables_async(self) -> None:
        return None

    async def _get_async_conn(self) -> "_PermissiveAsyncReceiver":
        return self

    async def _run_async_read(self, operation: Callable[[object], object]) -> object:
        result = operation(self)
        if inspect.isawaitable(result):
            return await cast(Awaitable[object], result)
        return result

    async def _run_async_write(self, *args: object, **kwargs: object) -> object:
        operation = kwargs.get("operation") or (args[0] if args else None)
        if not callable(operation):
            return _PermissiveRecord()
        result = operation(self)
        if inspect.isawaitable(result):
            return await cast(Awaitable[object], result)
        return result

    async def _call_sync_async(
        self,
        function: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        return function(*args, **kwargs)

    async def execute(self, *args: object, **kwargs: object) -> _PermissiveCursor:
        _ = (args, kwargs)
        return _PermissiveCursor()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def get_async(self, *args: object, **kwargs: object) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    async def upsert_async(self, *args: object, **kwargs: object) -> object:
        _ = kwargs
        return args[0] if args else _PermissiveRecord()

    async def update_async(self, *args: object, **kwargs: object) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    async def ensure_async(self, *args: object, **kwargs: object) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    async def list_by_session_async(
        self, *args: object, **kwargs: object
    ) -> tuple[_PermissiveRecord, ...]:
        _ = (args, kwargs)
        return (_PermissiveRecord(),)

    async def list_by_run_async(
        self, *args: object, **kwargs: object
    ) -> tuple[_PermissiveRecord, ...]:
        _ = (args, kwargs)
        return (_PermissiveRecord(),)

    async def list_by_trace_async(
        self, *args: object, **kwargs: object
    ) -> tuple[_PermissiveRecord, ...]:
        _ = (args, kwargs)
        return (_PermissiveRecord(),)

    async def list_running_async(
        self, *args: object, **kwargs: object
    ) -> tuple[_PermissiveRecord, ...]:
        _ = (args, kwargs)
        return (_PermissiveRecord(),)

    async def list_for_run_async(
        self, *args: object, **kwargs: object
    ) -> tuple[_PermissiveRecord, ...]:
        _ = (args, kwargs)
        return (_PermissiveRecord(),)

    async def get_instance_async(
        self, *args: object, **kwargs: object
    ) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    async def mark_completed_async(
        self, *args: object, **kwargs: object
    ) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    async def resolve_async(self, *args: object, **kwargs: object) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    async def publish_async(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        return None

    async def safe_publish_run_event_async(
        self, *args: object, **kwargs: object
    ) -> None:
        _ = (args, kwargs)
        return None

    async def execute_session_start_hooks(
        self, *args: object, **kwargs: object
    ) -> None:
        _ = (args, kwargs)
        return None

    async def execute_session_end_hooks(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        return None

    async def execute_stop_hooks(self, *args: object, **kwargs: object) -> bool:
        _ = (args, kwargs)
        return False

    async def execute_stop_failure_hooks(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        return None

    def _record_or_none(self, row: object) -> _PermissiveRecord:
        _ = row
        return _PermissiveRecord()

    def _row_to_record(self, row: object) -> _PermissiveRecord:
        _ = row
        return _PermissiveRecord()

    def _decode_record(self, row: object) -> _PermissiveRecord:
        _ = row
        return _PermissiveRecord()

    def _decode_event(self, row: object) -> dict[str, object]:
        _ = row
        return {"event_id": 1, "event_type": "x", "payload_json": "{}"}

    def _build_monitor_record(
        self, *args: object, **kwargs: object
    ) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    def _serialize_payload(self, *args: object, **kwargs: object) -> str:
        _ = (args, kwargs)
        return "{}"

    def _deserialize_payload(
        self, *args: object, **kwargs: object
    ) -> dict[str, object]:
        _ = (args, kwargs)
        return {}

    def current_request_prompt_content(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        return None

    def _should_delegate_to_bound_loop(self) -> bool:
        return False

    def _runtime_for_run(self, *args: object, **kwargs: object) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    async def _runtime_for_run_async(
        self, *args: object, **kwargs: object
    ) -> _PermissiveRecord:
        _ = (args, kwargs)
        return _PermissiveRecord()

    async def _worker(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        return None

    def is_run_stop_requested(self, *args: object, **kwargs: object) -> bool:
        _ = (args, kwargs)
        return False

    def _register_startup_gated_worker(
        self, *args: object, **kwargs: object
    ) -> tuple[asyncio.Event, asyncio.Event, asyncio.Event, asyncio.Task[None]]:
        _ = (args, kwargs)

        async def _noop() -> None:
            return None

        return (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
            asyncio.create_task(_noop()),
        )


_SYNC_METHOD_NAMES = {
    "activate",
    "append",
    "append_user_prompt_if_missing",
    "build_completed_error_run_result",
    "build_run_paused_payload",
    "clear_paused_subagent_for_run",
    "create_monitor",
    "deactivate",
    "drop_active_run",
    "emit",
    "emit_notification",
    "enqueue",
    "ensure",
    "get",
    "get_for_run",
    "get_instance",
    "handle_intent",
    "list_by_run",
    "list_by_session",
    "list_by_trace",
    "list_for_run",
    "list_running",
    "mark_started",
    "normalize_terminal_run_result",
    "publish",
    "register_run_task",
    "remember_active_run",
    "replace_pending_user_prompt",
    "request_run_stop",
    "resume_run",
    "run_media_generation",
    "run_with_auto_recovery",
    "safe_publish_run_event",
    "safe_runtime_update",
    "snapshot_run",
    "stop_all_for_run",
    "stop_background_task",
    "stop_for_run",
    "transition_run_to_resumed",
    "unregister_run_task",
    "update",
    "upsert",
}


def _permissive_value_for_parameter(name: str) -> object:
    if name == "reason":
        return "reason"
    if name == "action_type":
        return "wake_instance"
    if name == "source_kind":
        return "background_task"
    if name in {
        "action",
        "event_type",
        "kind",
        "phase",
        "scope",
        "source",
        "status",
    }:
        return _PermissiveValue("global" if name == "scope" else name)
    if name in {
        "answers",
        "ctx",
        "envelope",
        "event",
        "intent",
        "observation",
        "record",
        "request",
        "runtime",
        "snapshot",
        "state",
        "task",
        "task_record",
        "ticket",
    }:
        return _PermissiveRecord()
    if (
        name.startswith("is_")
        or name.startswith("include_")
        or name
        in {
            "allow_active_run_attach",
            "deep",
            "enqueue",
            "force",
            "reserve_user_prompt_tokens",
        }
    ):
        return False
    if name in {
        "attempt",
        "event_id",
        "limit",
        "max_attempts",
        "max_retries",
        "offset",
        "part_index",
        "safe_index",
        "timeout_ms",
        "time_window_minutes",
    }:
        return 1
    if name in {
        "allowed_mcp_servers",
        "allowed_skills",
        "allowed_tools",
        "history",
        "messages",
        "pending_messages",
        "questions",
        "records",
    }:
        return []
    if name in {"changes", "metadata", "payload", "results", "tags"}:
        return {}
    if name in {
        "created_at",
        "ended_at",
        "now",
        "resolved_at",
        "started_at",
        "updated_at",
    }:
        return datetime.now(tz=timezone.utc)
    if name.startswith(
        (
            "append_",
            "build_",
            "commit_",
            "create_",
            "emit_",
            "execute_",
            "get_",
            "history_",
            "is_",
            "last_",
            "log_",
            "mark_",
            "maybe_",
            "publish_",
            "record_",
            "request_",
            "sanitize_",
            "to_",
            "tool_",
            "update_",
        )
    ):
        return _callback_for_parameter(name)
    return f"{name}-value"


def _callback_for_parameter(name: str) -> Callable[..., object]:
    if "async" in name:

        async def async_callback(*args: object, **kwargs: object) -> bool:
            _ = (args, kwargs)
            return False

        return async_callback

    def sync_callback(*args: object, **kwargs: object) -> bool:
        _ = (args, kwargs)
        return False

    return sync_callback


def _permissive_arguments_for(
    signature: inspect.Signature,
) -> tuple[list[object], dict[str, object]]:
    args: list[object] = []
    kwargs: dict[str, object] = {}
    parameters = tuple(signature.parameters.values())
    for parameter in parameters[1:]:
        if parameter.default is not inspect.Parameter.empty:
            continue
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            continue
        value = _permissive_value_for_parameter(parameter.name)
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }:
            args.append(value)
        elif parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            kwargs[parameter.name] = value
    return args, kwargs


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize("spec", _PERMISSIVE_ASYNC_SPECS)
async def test_selected_async_migration_paths_execute_without_sync_dependencies(
    spec: _WrapperSpec,
) -> None:
    module = importlib.import_module(spec.module_name)
    class_object = _module_class(module=module, class_name=spec.class_name)
    method = cast(
        Callable[..., Awaitable[object]],
        getattr(class_object, spec.method_name),
    )
    receiver = _PermissiveAsyncReceiver()
    args, kwargs = _permissive_arguments_for(inspect.signature(method))

    result = method(receiver, *args, **kwargs)
    assert inspect.isawaitable(result)
    _ = await result
