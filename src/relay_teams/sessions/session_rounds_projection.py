from __future__ import annotations

from collections import Counter
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
import json
from typing import cast

from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.media import ContentPart
from relay_teams.media import ContentPartAdapter
from relay_teams.media import content_parts_from_text
from relay_teams.media import content_parts_to_text
from relay_teams.media import normalize_user_prompt_content
from relay_teams.media import text_part
from relay_teams.media import user_prompt_content_to_text
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRecord
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRecord
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.runs.terminal_payload import extract_terminal_output
from relay_teams.sessions.session_history_marker_models import (
    SessionHistoryMarkerType,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.workspace import build_conversation_id

ROUND_PROJECTION_EVENT_TYPES = (
    RunEventType.LLM_RETRY_SCHEDULED.value,
    RunEventType.LLM_RETRY_EXHAUSTED.value,
    RunEventType.LLM_FALLBACK_ACTIVATED.value,
    RunEventType.LLM_FALLBACK_EXHAUSTED.value,
    RunEventType.INJECTION_ENQUEUED.value,
    RunEventType.INJECTION_APPLIED.value,
    RunEventType.TOOL_CALL.value,
    RunEventType.TOOL_RESULT.value,
    RunEventType.MODEL_STEP_STARTED.value,
    RunEventType.MODEL_STEP_FINISHED.value,
    RunEventType.RUN_COMPLETED.value,
    RunEventType.RUN_FAILED.value,
    RunEventType.RUN_STOPPED.value,
)


def build_session_rounds(
    *,
    session_id: str,
    agent_repo: AgentInstanceRepository,
    task_repo: TaskRepository,
    approval_tickets_by_run: dict[str, list[dict[str, object]]],
    run_runtime_repo: RunRuntimeRepository,
    get_session_messages: Callable[[str], list[dict[str, object]]],
    get_run_intent_input: Callable[[str], tuple[ContentPart, ...] | None] | None = None,
    get_session_history_markers: Callable[[str], list[dict[str, object]]] | None = None,
    get_session_events: Callable[[str], list[dict[str, object]]] | None = None,
    excluded_run_ids: set[str] | None = None,
    included_run_ids: set[str] | None = None,
    run_runtime_by_run: dict[str, RunRuntimeRecord] | None = None,
) -> list[dict[str, object]]:
    session_tasks = (
        task_repo.list_by_session_run_ids(session_id, tuple(included_run_ids))
        if included_run_ids is not None
        else task_repo.list_by_session(session_id)
    )
    session_agents = agent_repo.list_session_role_instances(session_id)
    session_messages = get_session_messages(session_id)
    session_markers = (
        get_session_history_markers(session_id) if get_session_history_markers else []
    )
    session_events = get_session_events(session_id) if get_session_events else []
    run_runtime = (
        dict(run_runtime_by_run)
        if run_runtime_by_run is not None
        else {
            record.run_id: record
            for record in run_runtime_repo.list_by_session(session_id)
        }
    )

    instance_role_by_session: dict[str, str] = {}
    role_instance_by_run: dict[str, dict[str, str]] = defaultdict(dict)
    for agent in session_agents:
        instance_role_by_session[agent.instance_id] = agent.role_id

    instance_role_by_run: dict[str, dict[str, str]] = defaultdict(dict)

    tasks_by_run: dict[str, list[object]] = defaultdict(list)
    root_task_by_run: dict[str, object] = {}
    delegated_tasks_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    task_instance_map_by_run: dict[str, dict[str, str]] = defaultdict(dict)
    task_status_map_by_run: dict[str, dict[str, str]] = defaultdict(dict)
    for task in session_tasks:
        run_id = task.envelope.trace_id
        tasks_by_run[run_id].append(task)
        task_status_map_by_run[run_id][task.envelope.task_id] = task.status.value
        if task.assigned_instance_id:
            task_instance_map_by_run[run_id][task.envelope.task_id] = (
                task.assigned_instance_id
            )
            instance_role_by_run[run_id][task.assigned_instance_id] = str(
                task.envelope.role_id or ""
            )
            if task.envelope.role_id:
                role_instance_by_run[run_id][task.envelope.role_id] = (
                    task.assigned_instance_id
                )
        if task.envelope.parent_task_id is None:
            root_task_by_run[run_id] = task
            continue
        delegated_tasks_by_run[run_id].append(
            {
                "task_id": task.envelope.task_id,
                "title": task.envelope.title or task.envelope.objective[:80],
                "assigned_role_id": task.envelope.role_id,
                "status": task.status.value,
                "assigned_instance_id": task.assigned_instance_id,
                "role_id": task.envelope.role_id,
                "instance_id": task.assigned_instance_id,
            }
        )

    messages_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    for message in session_messages:
        run_id = str(message.get("trace_id") or "")
        if not run_id:
            continue
        instance_id = str(message.get("instance_id") or "")
        if instance_id and not message.get("role_id"):
            role_id = str(message.get("agent_role_id") or "")
            if not role_id:
                role_id = instance_role_by_run.get(run_id, {}).get(instance_id)
            if not role_id:
                role_id = instance_role_by_session.get(instance_id)
            if role_id:
                message["role_id"] = role_id
        messages_by_run[run_id].append(message)

    retry_events_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    fallback_events_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    injection_messages_by_run = _project_injection_messages(session_events)
    tool_messages_by_run = _project_tool_messages_from_events(session_events)
    final_output_by_run = _project_terminal_final_outputs(session_events)
    microcompact_by_run: dict[str, dict[str, object]] = {}
    retry_clear_events = {
        RunEventType.MODEL_STEP_STARTED.value,
        RunEventType.MODEL_STEP_FINISHED.value,
        RunEventType.RUN_COMPLETED.value,
        RunEventType.RUN_STOPPED.value,
    }
    active_retry_by_run: dict[str, dict[str, object]] = {}
    sorted_session_events = sorted(
        session_events,
        key=lambda item: str(item.get("occurred_at") or ""),
    )
    for event in sorted_session_events:
        event_type = str(event.get("event_type") or "")
        run_id = str(event.get("trace_id") or "")
        if not run_id:
            continue
        if event_type == RunEventType.LLM_RETRY_SCHEDULED.value:
            payload = _parse_event_payload(event.get("payload_json"))
            active_retry_by_run[run_id] = {
                "kind": "retry",
                "occurred_at": str(event.get("occurred_at") or ""),
                "instance_id": payload.get("instance_id", ""),
                "role_id": payload.get("role_id", ""),
                "attempt_number": payload.get("attempt_number", 0),
                "total_attempts": payload.get("total_attempts", 0),
                "retry_in_ms": payload.get("retry_in_ms", 0),
                "phase": "scheduled",
                "is_active": True,
                "error_code": payload.get("error_code", ""),
                "error_message": payload.get("error_message", ""),
            }
            continue
        if event_type == RunEventType.LLM_RETRY_EXHAUSTED.value:
            payload = _parse_event_payload(event.get("payload_json"))
            active_retry_by_run[run_id] = {
                "kind": "retry",
                "occurred_at": str(event.get("occurred_at") or ""),
                "instance_id": payload.get("instance_id", ""),
                "role_id": payload.get("role_id", ""),
                "attempt_number": payload.get("attempt_number", 0),
                "total_attempts": payload.get("total_attempts", 0),
                "retry_in_ms": 0,
                "phase": "failed",
                "is_active": False,
                "error_code": payload.get("error_code", ""),
                "error_message": payload.get("error_message", ""),
            }
            continue
        if event_type == RunEventType.LLM_FALLBACK_ACTIVATED.value:
            payload = _parse_event_payload(event.get("payload_json"))
            fallback_events_by_run[run_id].append(
                {
                    "kind": "fallback",
                    "occurred_at": str(event.get("occurred_at") or ""),
                    "instance_id": payload.get("instance_id", ""),
                    "role_id": payload.get("role_id", ""),
                    "attempt_number": payload.get("attempt_number", 0),
                    "total_attempts": payload.get("total_attempts", 0),
                    "retry_in_ms": 0,
                    "phase": "activated",
                    "is_active": False,
                    "error_code": payload.get("reason", ""),
                    "error_message": "",
                    "from_profile_id": payload.get("from_profile_id", ""),
                    "to_profile_id": payload.get("to_profile_id", ""),
                    "from_provider": payload.get("from_provider", ""),
                    "to_provider": payload.get("to_provider", ""),
                    "from_model": payload.get("from_model", ""),
                    "to_model": payload.get("to_model", ""),
                    "hop": payload.get("hop", 0),
                    "strategy_id": payload.get("strategy_id", ""),
                }
            )
            continue
        if event_type == RunEventType.LLM_FALLBACK_EXHAUSTED.value:
            payload = _parse_event_payload(event.get("payload_json"))
            fallback_events_by_run[run_id].append(
                {
                    "kind": "fallback",
                    "occurred_at": str(event.get("occurred_at") or ""),
                    "instance_id": payload.get("instance_id", ""),
                    "role_id": payload.get("role_id", ""),
                    "attempt_number": payload.get("attempt_number", 0),
                    "total_attempts": payload.get("total_attempts", 0),
                    "retry_in_ms": 0,
                    "phase": "failed",
                    "is_active": False,
                    "error_code": payload.get("error_code", ""),
                    "error_message": payload.get("error_message", ""),
                    "from_profile_id": payload.get("from_profile_id", ""),
                    "from_provider": payload.get("from_provider", ""),
                    "from_model": payload.get("from_model", ""),
                    "hop": payload.get("hop", 0),
                }
            )
            continue
        if event_type in {
            RunEventType.MODEL_STEP_STARTED.value,
            RunEventType.MODEL_STEP_FINISHED.value,
        }:
            payload = _parse_event_payload(event.get("payload_json"))
            projected_microcompact = _project_microcompact_payload(payload)
            if projected_microcompact is not None:
                microcompact_by_run[run_id] = projected_microcompact
            elif _payload_reports_microcompact_state(payload):
                microcompact_by_run.pop(run_id, None)
        active_event = active_retry_by_run.get(run_id)
        if (
            active_event is not None
            and active_event.get("kind") == "retry"
            and _should_clear_active_retry_event(
                active_event,
                event_type=event_type,
                retry_clear_events=retry_clear_events,
            )
        ):
            active_retry_by_run.pop(run_id, None)
            continue
    for run_id, fallback_events in fallback_events_by_run.items():
        retry_events_by_run[run_id].extend(fallback_events)
    for run_id, retry_event in active_retry_by_run.items():
        retry_events_by_run[run_id].append(retry_event)

    run_ids = set(root_task_by_run.keys())
    run_ids.update(messages_by_run.keys())
    run_ids.update(retry_events_by_run.keys())
    run_ids.update(injection_messages_by_run.keys())
    run_ids.update(tool_messages_by_run.keys())
    run_ids.update(final_output_by_run.keys())
    run_ids.update(delegated_tasks_by_run.keys())
    run_ids.update(run_runtime.keys())
    if excluded_run_ids:
        run_ids.difference_update(excluded_run_ids)
    if included_run_ids is not None:
        run_ids.intersection_update(included_run_ids)

    rounds: list[dict[str, object]] = []
    for run_id in run_ids:
        root_task = root_task_by_run.get(run_id)
        run_messages = messages_by_run.get(run_id, [])
        intent_input_parts = (
            get_run_intent_input(run_id) if get_run_intent_input is not None else None
        )
        has_user_messages = any(
            str(message.get("role") or "") == "user" for message in run_messages
        ) or bool(intent_input_parts)
        coordinator_role_id = None
        coordinator_instance_id = None
        if root_task is not None:
            envelope = getattr(root_task, "envelope", None)
            candidate_role_id = getattr(envelope, "role_id", None)
            if isinstance(candidate_role_id, str) and candidate_role_id:
                coordinator_role_id = candidate_role_id
            candidate_instance_id = getattr(root_task, "assigned_instance_id", None)
            if isinstance(candidate_instance_id, str) and candidate_instance_id:
                coordinator_instance_id = candidate_instance_id
        if coordinator_instance_id is None and coordinator_role_id is not None:
            coordinator_instance_id = role_instance_by_run.get(run_id, {}).get(
                coordinator_role_id
            )
        coordinator_messages = [
            projected
            for message in run_messages
            if (
                projected := _round_coordinator_message_projection(
                    message, coordinator_role_id
                )
            )
            is not None
        ]
        coordinator_messages = _merge_event_tool_messages(
            coordinator_messages,
            _coordinator_event_tool_messages(
                tool_messages_by_run.get(run_id, []),
                coordinator_role_id=coordinator_role_id,
                coordinator_instance_id=coordinator_instance_id,
            ),
        )
        coordinator_messages = _append_completed_output_message_if_missing(
            coordinator_messages,
            run_id=run_id,
            root_task=root_task,
            coordinator_role_id=coordinator_role_id,
            role_instance_map=role_instance_by_run.get(run_id, {}),
            output_event=final_output_by_run.get(run_id),
        )
        created_at = _round_created_at(root_task, run_messages)
        runtime = run_runtime.get(run_id)
        run_started_at = runtime.created_at.isoformat() if runtime is not None else None
        run_updated_at = runtime.updated_at.isoformat() if runtime is not None else None
        pending_approvals = list(approval_tickets_by_run.get(run_id, []))
        intent_parts = _round_intent_parts(
            root_task,
            run_messages,
            intent_input_parts=intent_input_parts,
        )
        round_item: dict[str, object] = {
            "run_id": run_id,
            "created_at": created_at,
            "intent": _round_intent(root_task, run_messages, intent_parts=intent_parts),
            "intent_parts": intent_parts,
            "primary_role_id": coordinator_role_id,
            "coordinator_messages": coordinator_messages,
            "injection_messages": injection_messages_by_run.get(run_id, []),
            "retry_events": retry_events_by_run.get(run_id, []),
            "has_user_messages": has_user_messages,
            "tasks": delegated_tasks_by_run.get(run_id, []),
            "instance_role_map": instance_role_by_run.get(run_id, {}),
            "role_instance_map": role_instance_by_run.get(run_id, {}),
            "task_instance_map": task_instance_map_by_run.get(run_id, {}),
            "task_status_map": task_status_map_by_run.get(run_id, {}),
            "pending_tool_approvals": pending_approvals,
            "pending_tool_approval_count": len(pending_approvals),
            "run_started_at": run_started_at,
            "run_updated_at": run_updated_at,
            "run_status": runtime.status.value if runtime is not None else None,
            "run_phase": runtime.phase.value if runtime is not None else None,
            "has_final_output": run_id in final_output_by_run,
            "is_recoverable": runtime.is_recoverable if runtime is not None else False,
            "clear_marker_before": None,
            "compaction_marker_before": None,
            "microcompact": microcompact_by_run.get(run_id),
        }
        rounds.append(round_item)

    rounds.sort(key=lambda item: str(item.get("created_at") or ""))
    _attach_history_markers(
        session_id=session_id, rounds=rounds, session_markers=session_markers
    )
    rounds.reverse()
    return rounds


def build_session_timeline_rounds(
    *,
    session_id: str,
    task_repo: TaskRepository,
    approval_tickets_by_run: dict[str, list[dict[str, object]]],
    run_runtime_repo: RunRuntimeRepository,
    get_session_user_messages: Callable[[str], list[dict[str, object]]],
    get_run_intent_input: Callable[[str], tuple[ContentPart, ...] | None] | None = None,
    get_session_history_markers: Callable[[str], list[dict[str, object]]] | None = None,
    get_session_events: Callable[[str], list[dict[str, object]]] | None = None,
    excluded_run_ids: set[str] | None = None,
    included_run_ids: set[str] | None = None,
    run_runtime_by_run: dict[str, RunRuntimeRecord] | None = None,
) -> list[dict[str, object]]:
    session_tasks = (
        task_repo.list_by_session_run_ids(session_id, tuple(included_run_ids))
        if included_run_ids is not None
        else task_repo.list_by_session(session_id)
    )
    session_messages = get_session_user_messages(session_id)
    session_markers = (
        get_session_history_markers(session_id) if get_session_history_markers else []
    )
    session_events = get_session_events(session_id) if get_session_events else []
    run_runtime = (
        dict(run_runtime_by_run)
        if run_runtime_by_run is not None
        else {
            record.run_id: record
            for record in run_runtime_repo.list_by_session(session_id)
        }
    )
    retry_events_by_run, microcompact_by_run = _project_timeline_event_overlays(
        session_events
    )
    injection_messages_by_run = _project_injection_messages(session_events)
    final_output_by_run = _project_terminal_final_outputs(session_events)

    root_task_by_run: dict[str, object] = {}
    primary_role_by_run: dict[str, str] = {}
    for task in session_tasks:
        run_id = task.envelope.trace_id
        if task.envelope.parent_task_id is not None:
            continue
        root_task_by_run[run_id] = task
        if task.envelope.role_id:
            primary_role_by_run[run_id] = task.envelope.role_id

    messages_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    for message in session_messages:
        run_id = str(message.get("trace_id") or "")
        if run_id:
            messages_by_run[run_id].append(message)

    run_ids = set(root_task_by_run.keys())
    run_ids.update(messages_by_run.keys())
    run_ids.update(retry_events_by_run.keys())
    run_ids.update(injection_messages_by_run.keys())
    run_ids.update(final_output_by_run.keys())
    run_ids.update(run_runtime.keys())
    if excluded_run_ids:
        run_ids.difference_update(excluded_run_ids)
    if included_run_ids is not None:
        run_ids.intersection_update(included_run_ids)

    rounds: list[dict[str, object]] = []
    for run_id in run_ids:
        root_task = root_task_by_run.get(run_id)
        run_messages = messages_by_run.get(run_id, [])
        intent_input_parts = (
            get_run_intent_input(run_id) if get_run_intent_input is not None else None
        )
        intent_parts = _round_intent_parts(
            root_task,
            run_messages,
            intent_input_parts=intent_input_parts,
        )
        runtime = run_runtime.get(run_id)
        run_started_at = runtime.created_at.isoformat() if runtime is not None else None
        run_updated_at = runtime.updated_at.isoformat() if runtime is not None else None
        pending_approvals = list(approval_tickets_by_run.get(run_id, []))
        rounds.append(
            {
                "run_id": run_id,
                "created_at": _round_created_at(root_task, run_messages),
                "intent": _round_intent(
                    root_task, run_messages, intent_parts=intent_parts
                ),
                "intent_parts": intent_parts,
                "primary_role_id": primary_role_by_run.get(run_id),
                "injection_messages": injection_messages_by_run.get(run_id, []),
                "retry_events": retry_events_by_run.get(run_id, []),
                "has_user_messages": bool(run_messages) or bool(intent_input_parts),
                "pending_tool_approval_count": len(pending_approvals),
                "run_started_at": run_started_at,
                "run_updated_at": run_updated_at,
                "run_status": runtime.status.value if runtime is not None else None,
                "run_phase": runtime.phase.value if runtime is not None else None,
                "has_final_output": run_id in final_output_by_run,
                "is_recoverable": (
                    runtime.is_recoverable if runtime is not None else False
                ),
                "clear_marker_before": None,
                "compaction_marker_before": None,
                "microcompact": microcompact_by_run.get(run_id),
            }
        )

    rounds.sort(key=lambda item: str(item.get("created_at") or ""))
    _attach_history_markers(
        session_id=session_id, rounds=rounds, session_markers=session_markers
    )
    rounds.reverse()
    return rounds


def paginate_rounds(
    rounds: list[dict[str, object]],
    *,
    limit: int = 8,
    cursor_run_id: str | None = None,
) -> dict[str, object]:
    safe_limit = max(1, min(limit, 50))
    start = 0
    if cursor_run_id:
        for idx, item in enumerate(rounds):
            if item.get("run_id") == cursor_run_id:
                start = idx + 1
                break
    items = rounds[start : start + safe_limit]
    next_index = start + safe_limit
    has_more = next_index < len(rounds)
    next_cursor = items[-1]["run_id"] if has_more and items else None
    return {
        "items": items,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def _project_timeline_event_overlays(
    session_events: list[dict[str, object]],
) -> tuple[dict[str, list[dict[str, object]]], dict[str, dict[str, object]]]:
    retry_events_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    fallback_events_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    microcompact_by_run: dict[str, dict[str, object]] = {}
    active_retry_by_run: dict[str, dict[str, object]] = {}
    retry_clear_events = {
        RunEventType.MODEL_STEP_STARTED.value,
        RunEventType.MODEL_STEP_FINISHED.value,
        RunEventType.RUN_COMPLETED.value,
        RunEventType.RUN_STOPPED.value,
    }
    sorted_session_events = sorted(
        session_events,
        key=lambda item: str(item.get("occurred_at") or ""),
    )
    for event in sorted_session_events:
        event_type = str(event.get("event_type") or "")
        run_id = str(event.get("trace_id") or "")
        if not run_id:
            continue
        if event_type == RunEventType.LLM_RETRY_SCHEDULED.value:
            payload = _parse_event_payload(event.get("payload_json"))
            active_retry_by_run[run_id] = {
                "kind": "retry",
                "occurred_at": str(event.get("occurred_at") or ""),
                "instance_id": payload.get("instance_id", ""),
                "role_id": payload.get("role_id", ""),
                "attempt_number": payload.get("attempt_number", 0),
                "total_attempts": payload.get("total_attempts", 0),
                "retry_in_ms": payload.get("retry_in_ms", 0),
                "phase": "scheduled",
                "is_active": True,
                "error_code": payload.get("error_code", ""),
                "error_message": payload.get("error_message", ""),
            }
            continue
        if event_type == RunEventType.LLM_RETRY_EXHAUSTED.value:
            payload = _parse_event_payload(event.get("payload_json"))
            active_retry_by_run[run_id] = {
                "kind": "retry",
                "occurred_at": str(event.get("occurred_at") or ""),
                "instance_id": payload.get("instance_id", ""),
                "role_id": payload.get("role_id", ""),
                "attempt_number": payload.get("attempt_number", 0),
                "total_attempts": payload.get("total_attempts", 0),
                "retry_in_ms": 0,
                "phase": "failed",
                "is_active": False,
                "error_code": payload.get("error_code", ""),
                "error_message": payload.get("error_message", ""),
            }
            continue
        if event_type == RunEventType.LLM_FALLBACK_ACTIVATED.value:
            payload = _parse_event_payload(event.get("payload_json"))
            fallback_events_by_run[run_id].append(
                {
                    "kind": "fallback",
                    "occurred_at": str(event.get("occurred_at") or ""),
                    "instance_id": payload.get("instance_id", ""),
                    "role_id": payload.get("role_id", ""),
                    "attempt_number": payload.get("attempt_number", 0),
                    "total_attempts": payload.get("total_attempts", 0),
                    "retry_in_ms": 0,
                    "phase": "activated",
                    "is_active": False,
                    "error_code": payload.get("reason", ""),
                    "error_message": "",
                    "from_profile_id": payload.get("from_profile_id", ""),
                    "to_profile_id": payload.get("to_profile_id", ""),
                    "from_provider": payload.get("from_provider", ""),
                    "to_provider": payload.get("to_provider", ""),
                    "from_model": payload.get("from_model", ""),
                    "to_model": payload.get("to_model", ""),
                    "hop": payload.get("hop", 0),
                    "strategy_id": payload.get("strategy_id", ""),
                }
            )
            continue
        if event_type == RunEventType.LLM_FALLBACK_EXHAUSTED.value:
            payload = _parse_event_payload(event.get("payload_json"))
            fallback_events_by_run[run_id].append(
                {
                    "kind": "fallback",
                    "occurred_at": str(event.get("occurred_at") or ""),
                    "instance_id": payload.get("instance_id", ""),
                    "role_id": payload.get("role_id", ""),
                    "attempt_number": payload.get("attempt_number", 0),
                    "total_attempts": payload.get("total_attempts", 0),
                    "retry_in_ms": 0,
                    "phase": "failed",
                    "is_active": False,
                    "error_code": payload.get("error_code", ""),
                    "error_message": payload.get("error_message", ""),
                    "from_profile_id": payload.get("from_profile_id", ""),
                    "from_provider": payload.get("from_provider", ""),
                    "from_model": payload.get("from_model", ""),
                    "hop": payload.get("hop", 0),
                }
            )
            continue
        if event_type in {
            RunEventType.MODEL_STEP_STARTED.value,
            RunEventType.MODEL_STEP_FINISHED.value,
        }:
            payload = _parse_event_payload(event.get("payload_json"))
            projected_microcompact = _project_microcompact_payload(payload)
            if projected_microcompact is not None:
                microcompact_by_run[run_id] = projected_microcompact
            elif _payload_reports_microcompact_state(payload):
                microcompact_by_run.pop(run_id, None)
        active_event = active_retry_by_run.get(run_id)
        if (
            active_event is not None
            and active_event.get("kind") == "retry"
            and _should_clear_active_retry_event(
                active_event,
                event_type=event_type,
                retry_clear_events=retry_clear_events,
            )
        ):
            active_retry_by_run.pop(run_id, None)
            continue
    for run_id, fallback_events in fallback_events_by_run.items():
        retry_events_by_run[run_id].extend(fallback_events)
    for run_id, retry_event in active_retry_by_run.items():
        retry_events_by_run[run_id].append(retry_event)
    return dict(retry_events_by_run), microcompact_by_run


def _project_injection_messages(
    session_events: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    projected_by_run: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    sorted_session_events = sorted(
        session_events,
        key=lambda item: str(item.get("occurred_at") or ""),
    )
    for event in sorted_session_events:
        event_type = str(event.get("event_type") or "")
        if event_type != RunEventType.INJECTION_APPLIED.value:
            continue
        run_id = str(event.get("trace_id") or event.get("run_id") or "")
        if not run_id:
            continue
        payload = _parse_event_payload(event.get("payload_json"))
        if not _is_public_injection_payload(payload):
            continue
        recipient_instance_id = str(
            payload.get("recipient_instance_id") or event.get("instance_id") or ""
        )
        created_at = str(payload.get("created_at") or event.get("occurred_at") or "")
        content = payload.get("content")
        content_parts = _coerce_user_prompt_content_parts(content)
        content_text = user_prompt_content_to_text(content).strip()
        if not content_text and content_parts:
            content_text = _intent_parts_to_text(content_parts) or ""
        if not content_text and not content_parts:
            continue
        key = "|".join(
            (
                str(payload.get("injection_id") or ""),
                recipient_instance_id,
                created_at,
                str(payload.get("source") or ""),
                content_text,
            )
        )
        existing = projected_by_run[run_id].get(key)
        if existing is None:
            existing = {
                "message_id": key,
                "injection_id": str(payload.get("injection_id") or key),
                "run_id": run_id,
                "source": str(payload.get("source") or "user"),
                "mode": str(payload.get("delivery_mode") or "queued"),
                "status": "applied",
                "content": content_text,
                "content_parts": content_parts or [],
                "recipient_instance_id": recipient_instance_id,
                "sender_instance_id": str(payload.get("sender_instance_id") or ""),
                "sender_role_id": str(payload.get("sender_role_id") or ""),
                "queued_at": created_at,
                "applied_at": str(event.get("occurred_at") or created_at),
                "occurred_at": str(event.get("occurred_at") or created_at),
                "interrupted_current_step": payload.get("interrupted_current_step")
                is True,
                "restart_scope": str(payload.get("restart_scope") or ""),
                "supersedes_pending_tool_calls": payload.get(
                    "supersedes_pending_tool_calls"
                )
                is True,
            }
            projected_by_run[run_id][key] = existing
    return {
        run_id: sorted(
            items.values(),
            key=lambda item: str(
                item.get("applied_at") or item.get("occurred_at") or ""
            ),
        )
        for run_id, items in projected_by_run.items()
    }


def _is_public_injection_payload(payload: dict[str, object]) -> bool:
    if str(payload.get("visibility") or "public") != "public":
        return False
    if payload.get("content_redacted"):
        return False
    source = str(payload.get("source") or "")
    return source in {"user", "subagent"}


def _project_tool_messages_from_events(
    session_events: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    calls_by_key: dict[tuple[str, int], dict[str, object]] = {}
    results_by_key: dict[tuple[str, int], dict[str, object]] = {}
    order_by_run: dict[str, list[tuple[str, int]]] = defaultdict(list)
    pending_by_identity: dict[tuple[str, str, str, str], list[tuple[str, int]]] = (
        defaultdict(list)
    )
    stopped_run_ids = _stopped_run_ids_from_events(session_events)
    sorted_session_events = sorted(
        session_events,
        key=lambda item: str(item.get("occurred_at") or ""),
    )
    for event in sorted_session_events:
        event_type = str(event.get("event_type") or "")
        if event_type not in {
            RunEventType.TOOL_CALL.value,
            RunEventType.TOOL_RESULT.value,
        }:
            continue
        payload = _parse_event_payload(event.get("payload_json"))
        run_id = str(
            event.get("trace_id") or payload.get("run_id") or event.get("run_id") or ""
        )
        tool_call_id = str(payload.get("tool_call_id") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        role_id = str(payload.get("role_id") or event.get("role_id") or "")
        instance_id = str(payload.get("instance_id") or event.get("instance_id") or "")
        if not run_id or not tool_call_id or not tool_name:
            continue
        identity_key = (run_id, instance_id, role_id, tool_call_id)
        if event_type == RunEventType.TOOL_CALL.value:
            record_key = (run_id, len(order_by_run[run_id]))
            calls_by_key[record_key] = {
                "run_id": run_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "args": payload.get("args") or {},
                "role_id": role_id,
                "instance_id": instance_id,
                "task_id": str(event.get("task_id") or ""),
                "occurred_at": str(event.get("occurred_at") or ""),
            }
            order_by_run[run_id].append(record_key)
            pending_by_identity[identity_key].append(record_key)
            continue
        pending_keys = pending_by_identity.get(identity_key, [])
        if not pending_keys:
            continue
        record_key = pending_keys.pop(0)
        results_by_key[record_key] = {
            "run_id": run_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "result": payload.get("result"),
            "error": payload.get("error") is True,
            "role_id": role_id,
            "instance_id": instance_id,
            "task_id": str(event.get("task_id") or ""),
            "occurred_at": str(event.get("occurred_at") or ""),
        }

    projected_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    for run_id, tool_call_keys in order_by_run.items():
        for record_key in tool_call_keys:
            call = calls_by_key.get(record_key)
            result = results_by_key.get(record_key)
            if call is None:
                continue
            if result is None:
                if _should_project_unmatched_tool_call(
                    call,
                    stopped_run_ids=stopped_run_ids,
                ):
                    projected_by_run[run_id].append(_event_tool_call_message(call))
                continue
            call_tool_name = str(call.get("tool_name") or "")
            result_tool_name = str(result.get("tool_name") or "")
            if (
                call_tool_name
                and result_tool_name
                and call_tool_name != result_tool_name
            ):
                continue
            projected_by_run[run_id].append(_event_tool_call_message(call))
            projected_by_run[run_id].append(_event_tool_result_message(result))
    return dict(projected_by_run)


def _stopped_run_ids_from_events(
    session_events: list[dict[str, object]],
) -> set[str]:
    stopped_run_ids: set[str] = set()
    for event in session_events:
        if str(event.get("event_type") or "") != RunEventType.RUN_STOPPED.value:
            continue
        run_id = str(event.get("trace_id") or event.get("run_id") or "").strip()
        if run_id:
            stopped_run_ids.add(run_id)
    return stopped_run_ids


def _should_project_unmatched_tool_call(
    call: dict[str, object],
    *,
    stopped_run_ids: set[str],
) -> bool:
    run_id = str(call.get("run_id") or "").strip()
    if run_id not in stopped_run_ids:
        return False
    return str(call.get("tool_name") or "").strip() == "spawn_subagent"


def _event_tool_call_message(record: dict[str, object]) -> dict[str, object]:
    run_id = str(record.get("run_id") or "")
    tool_call_id = str(record.get("tool_call_id") or "")
    tool_name = str(record.get("tool_name") or "")
    role_id = str(record.get("role_id") or "")
    instance_id = str(record.get("instance_id") or "")
    occurred_at = str(record.get("occurred_at") or "")
    return {
        "conversation_id": "",
        "agent_role_id": role_id,
        "instance_id": instance_id,
        "task_id": str(record.get("task_id") or ""),
        "trace_id": run_id,
        "role": "assistant",
        "role_id": role_id,
        "created_at": occurred_at,
        "reconstructed": True,
        "message": {
            "parts": [
                {
                    "part_kind": "tool-call",
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "args": record.get("args") or {},
                }
            ]
        },
    }


def _event_tool_result_message(record: dict[str, object]) -> dict[str, object]:
    run_id = str(record.get("run_id") or "")
    tool_call_id = str(record.get("tool_call_id") or "")
    tool_name = str(record.get("tool_name") or "")
    role_id = str(record.get("role_id") or "")
    instance_id = str(record.get("instance_id") or "")
    occurred_at = str(record.get("occurred_at") or "")
    return {
        "conversation_id": "",
        "agent_role_id": role_id,
        "instance_id": instance_id,
        "task_id": str(record.get("task_id") or ""),
        "trace_id": run_id,
        "role": "user",
        "role_id": role_id,
        "created_at": occurred_at,
        "reconstructed": True,
        "message": {
            "parts": [
                {
                    "part_kind": "tool-return",
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": record.get("result"),
                    "is_error": record.get("error") is True,
                }
            ]
        },
    }


def _coordinator_event_tool_messages(
    messages: list[dict[str, object]],
    *,
    coordinator_role_id: str | None,
    coordinator_instance_id: str | None,
) -> list[dict[str, object]]:
    if coordinator_role_id is None and coordinator_instance_id is None:
        return []
    filtered: list[dict[str, object]] = []
    for message in messages:
        role_id = str(message.get("role_id") or message.get("agent_role_id") or "")
        instance_id = str(message.get("instance_id") or "")
        if coordinator_instance_id is not None:
            if instance_id != coordinator_instance_id:
                continue
            if (
                coordinator_role_id is None
                or not role_id
                or role_id == coordinator_role_id
            ):
                filtered.append(message)
            continue
        if coordinator_role_id is not None and role_id == coordinator_role_id:
            filtered.append(message)
    return filtered


def _merge_event_tool_messages(
    coordinator_messages: list[dict[str, object]],
    event_tool_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not event_tool_messages:
        return coordinator_messages
    existing_tool_part_counts = _message_tool_part_counts(coordinator_messages)
    event_tool_part_counts: Counter[tuple[str, str]] = Counter()
    missing: list[dict[str, object]] = []
    for message in event_tool_messages:
        include_message = False
        for key in _message_tool_part_keys(message):
            event_tool_part_counts[key] += 1
            if event_tool_part_counts[key] > existing_tool_part_counts[key]:
                include_message = True
        if include_message:
            missing.append(message)
    if not missing:
        return coordinator_messages
    return sorted(
        [*coordinator_messages, *missing],
        key=lambda item: (
            str(item.get("created_at") or ""),
            1 if str(item.get("role") or "") == "user" else 0,
        ),
    )


def _message_tool_part_counts(
    messages: list[dict[str, object]],
) -> Counter[tuple[str, str]]:
    tool_part_counts: Counter[tuple[str, str]] = Counter()
    for message in messages:
        tool_part_counts.update(_message_tool_part_keys(message))
    return tool_part_counts


def _message_tool_part_keys(message: dict[str, object]) -> list[tuple[str, str]]:
    tool_part_keys: list[tuple[str, str]] = []
    for part in _message_parts(message):
        part_kind = str(part.get("part_kind") or "").strip()
        tool_call_id = str(part.get("tool_call_id") or "").strip()
        if part_kind and tool_call_id:
            tool_part_keys.append((part_kind, tool_call_id))
    return tool_part_keys


def _message_parts(message: dict[str, object]) -> list[dict[str, object]]:
    raw_message = message.get("message")
    if not isinstance(raw_message, dict):
        return []
    raw_parts = raw_message.get("parts")
    if not isinstance(raw_parts, list):
        return []
    return [part for part in raw_parts if isinstance(part, dict)]


def _append_completed_output_message_if_missing(
    coordinator_messages: list[dict[str, object]],
    *,
    run_id: str,
    root_task: object | None,
    coordinator_role_id: str | None,
    role_instance_map: dict[str, str],
    output_event: dict[str, str] | None,
) -> list[dict[str, object]]:
    reconstructed = _reconstruct_completed_output_message(
        run_id=run_id,
        root_task=root_task,
        coordinator_role_id=coordinator_role_id,
        role_instance_map=role_instance_map,
        output_event=output_event,
    )
    if reconstructed is None:
        return coordinator_messages
    output = str(output_event.get("output") if output_event is not None else "").strip()
    if _has_assistant_text_message(coordinator_messages, output):
        return coordinator_messages
    return [*coordinator_messages, reconstructed]


def _has_assistant_text_message(
    messages: list[dict[str, object]],
    expected_text: str,
) -> bool:
    normalized_expected = _normalize_projected_text(expected_text)
    if not normalized_expected:
        return False
    for message in messages:
        if str(message.get("role") or "") != "assistant":
            continue
        text_parts = tuple(_message_text_parts(message))
        if not text_parts:
            continue
        if any(
            _normalize_projected_text(part_text) == normalized_expected
            for part_text in text_parts
        ):
            return True
        combined = "\n\n".join(
            part_text.strip() for part_text in text_parts if part_text.strip()
        )
        if _normalize_projected_text(combined) == normalized_expected:
            return True
    return False


def _message_text_parts(message: dict[str, object]) -> list[str]:
    values: list[str] = []
    raw_message = message.get("message")
    if isinstance(raw_message, dict):
        legacy_content = str(raw_message.get("content") or "").strip()
        if legacy_content:
            values.append(legacy_content)
    for part in _message_parts(message):
        part_kind = str(part.get("part_kind") or part.get("kind") or "").strip()
        if part_kind != "text":
            continue
        value = str(part.get("content") or part.get("text") or "").strip()
        if value:
            values.append(value)
    return values


def _normalize_projected_text(value: str) -> str:
    return str(value or "").strip()


def _project_terminal_final_outputs(
    session_events: list[dict[str, object]],
) -> dict[str, dict[str, str]]:
    final_output_by_run: dict[str, dict[str, str]] = {}
    sorted_session_events = sorted(
        session_events,
        key=lambda item: str(item.get("occurred_at") or ""),
    )
    for event in sorted_session_events:
        event_type = str(event.get("event_type") or "")
        if event_type not in {
            RunEventType.RUN_COMPLETED.value,
            RunEventType.RUN_FAILED.value,
        }:
            continue
        run_id = str(event.get("trace_id") or "")
        if not run_id:
            continue
        payload = _parse_event_payload(event.get("payload_json"))
        output = extract_terminal_output(payload).strip()
        if not _event_has_final_output(
            event_type=event_type,
            payload=payload,
            output=output,
        ):
            continue
        final_output_by_run[run_id] = {
            "output": output,
            "occurred_at": str(event.get("occurred_at") or ""),
        }
    return final_output_by_run


def _event_has_final_output(
    *,
    event_type: str,
    payload: dict[str, object],
    output: str,
) -> bool:
    if not output:
        return False
    if event_type == RunEventType.RUN_COMPLETED.value:
        return True
    if event_type != RunEventType.RUN_FAILED.value:
        return False
    completion_reason = str(payload.get("completion_reason") or "").strip().lower()
    return completion_reason == RunCompletionReason.ASSISTANT_RESPONSE.value


def _should_clear_active_retry_event(
    active_event: dict[str, object],
    *,
    event_type: str,
    retry_clear_events: set[str],
) -> bool:
    if event_type in retry_clear_events:
        return True
    if event_type != RunEventType.RUN_FAILED.value:
        return False
    return str(active_event.get("phase") or "") != "failed"


_TIMELINE_ROUND_KEYS = (
    "run_id",
    "created_at",
    "intent",
    "intent_parts",
    "primary_role_id",
    "injection_messages",
    "retry_events",
    "has_user_messages",
    "pending_tool_approval_count",
    "run_started_at",
    "run_updated_at",
    "run_status",
    "run_phase",
    "has_final_output",
    "is_recoverable",
    "clear_marker_before",
    "compaction_marker_before",
    "microcompact",
    "todo",
    "tool_call_count",
    "total_tool_calls",
)


def timeline_rounds(
    rounds: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "items": [
            {key: round_item[key] for key in _TIMELINE_ROUND_KEYS if key in round_item}
            for round_item in rounds
        ],
        "has_more": False,
        "next_cursor": None,
    }


def find_round_by_run_id(
    rounds: list[dict[str, object]],
    *,
    session_id: str,
    run_id: str,
) -> dict[str, object]:
    for round_item in rounds:
        if round_item["run_id"] == run_id:
            return round_item
    raise KeyError(f"Round {run_id} not found in session {session_id}")


def approvals_to_projection(
    approvals: tuple[ApprovalTicketRecord, ...] | list[ApprovalTicketRecord],
) -> dict[str, list[dict[str, object]]]:
    by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in approvals:
        by_run[record.run_id].append(
            {
                "tool_call_id": record.tool_call_id,
                "tool_name": record.tool_name,
                "args_preview": record.args_preview,
                "role_id": record.role_id,
                "instance_id": record.instance_id,
                "requested_at": record.created_at.isoformat(),
                "status": record.status.value,
                "feedback": record.feedback,
            }
        )
    for items in by_run.values():
        items.sort(key=lambda item: str(item.get("requested_at") or ""))
    return dict(by_run)


def _round_created_at(root_task: object, run_messages: list[dict[str, object]]) -> str:
    if root_task is not None:
        created_at = getattr(root_task, "created_at", None)
        if created_at is not None:
            return created_at.isoformat()
    if run_messages:
        return str(run_messages[0].get("created_at") or "")
    return ""


def _round_intent(
    root_task: object,
    run_messages: list[dict[str, object]],
    *,
    intent_parts: list[dict[str, object]] | None = None,
) -> str | None:
    if intent_parts:
        prompt = _intent_parts_to_text(intent_parts)
        if prompt:
            return prompt
    prompt_projection = _extract_round_user_prompt_parts(run_messages)
    if prompt_projection:
        prompt = _intent_parts_to_text(prompt_projection)
        if prompt:
            return prompt
    if root_task is not None:
        envelope = getattr(root_task, "envelope", None)
        objective = getattr(envelope, "objective", None)
        if isinstance(objective, str) and objective.strip():
            return objective
    return None


def _round_intent_parts(
    root_task: object,
    run_messages: list[dict[str, object]],
    *,
    intent_input_parts: tuple[ContentPart, ...] | None = None,
) -> list[dict[str, object]] | None:
    if intent_input_parts:
        return _content_parts_to_projection(intent_input_parts)
    prompt_projection = _extract_round_user_prompt_parts(run_messages)
    if prompt_projection:
        return prompt_projection
    if root_task is not None:
        envelope = getattr(root_task, "envelope", None)
        objective = getattr(envelope, "objective", None)
        if isinstance(objective, str) and objective.strip():
            objective_parts = content_parts_from_text(objective)
            if objective_parts:
                return _content_parts_to_projection(objective_parts)
    return None


def _extract_round_user_prompt_parts(
    run_messages: list[dict[str, object]],
) -> list[dict[str, object]] | None:
    for message in run_messages:
        if str(message.get("role") or "") != "user":
            continue
        prompt_projection = _extract_user_prompt_parts(
            cast(object, message.get("message"))
        )
        if prompt_projection:
            return prompt_projection
    return None


def _extract_user_prompt_parts(message: object) -> list[dict[str, object]] | None:
    if not isinstance(message, dict):
        return None
    parts = message.get("parts")
    if not isinstance(parts, list):
        return None
    for part in parts:
        if not isinstance(part, dict):
            continue
        if str(part.get("part_kind") or "") != "user-prompt":
            continue
        prompt_projection = _coerce_user_prompt_content_parts(part.get("content"))
        if prompt_projection:
            return prompt_projection
    return None


def _coerce_user_prompt_content_parts(
    content: object,
) -> list[dict[str, object]] | None:
    if isinstance(content, str):
        text_parts = content_parts_from_text(content)
        return _content_parts_to_projection(text_parts) if text_parts else None
    if not isinstance(content, list):
        return None
    prompt_parts: list[dict[str, object]] = []
    for item in content:
        projection_item = _coerce_user_prompt_content_projection_item(item)
        if projection_item is None:
            continue
        prompt_parts.append(projection_item)
    return prompt_parts or None


def _coerce_user_prompt_content_projection_item(
    item: object,
) -> dict[str, object] | None:
    if isinstance(item, str):
        part = text_part(item)
        if part is None:
            return None
        return cast(dict[str, object], part.model_dump(mode="json"))
    if isinstance(item, dict):
        raw_item = cast(dict[str, object], item)
        if _is_binary_prompt_payload(raw_item):
            return _binary_prompt_payload_projection(raw_item)
    try:
        validated = ContentPartAdapter.validate_python(item)
    except Exception:
        pass
    else:
        return cast(dict[str, object], validated.model_dump(mode="json"))

    normalized = normalize_user_prompt_content(item)
    if isinstance(normalized, str):
        part = text_part(normalized)
        if part is None:
            return None
        return cast(dict[str, object], part.model_dump(mode="json"))
    if not isinstance(normalized, dict):
        return None

    projection_item = dict(cast(dict[str, object], normalized))
    label = str(projection_item.get("label") or "").strip()
    if label and not str(projection_item.get("name") or "").strip():
        projection_item["name"] = label
    return projection_item


def _is_binary_prompt_payload(item: dict[str, object]) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    media_type = str(item.get("media_type") or item.get("mediaType") or "").strip()
    data = item.get("data")
    return (
        kind == "binary" and media_type != "" and isinstance(data, str) and data != ""
    )


def _binary_prompt_payload_projection(item: dict[str, object]) -> dict[str, object]:
    projection_item = dict(item)
    media_type = str(
        projection_item.get("media_type") or projection_item.get("mediaType") or ""
    ).strip()
    if str(projection_item.get("media_type") or "").strip() == "":
        projection_item["media_type"] = media_type
    if str(projection_item.get("modality") or "").strip() == "":
        lowered_media_type = media_type.lower()
        projection_item["modality"] = (
            "audio"
            if lowered_media_type.startswith("audio/")
            else "video"
            if lowered_media_type.startswith("video/")
            else "image"
        )
    name = str(projection_item.get("name") or "").strip()
    if name and str(projection_item.get("label") or "").strip() == "":
        projection_item["label"] = name
    return projection_item


def _content_parts_to_projection(
    parts: tuple[ContentPart, ...],
) -> list[dict[str, object]] | None:
    payload = [cast(dict[str, object], part.model_dump(mode="json")) for part in parts]
    if not payload:
        return None
    return payload


def _content_parts_to_projection_text(parts: tuple[ContentPart, ...]) -> str | None:
    text = content_parts_to_text(parts)
    return text.strip() or None


def _intent_parts_to_text(intent_parts: list[dict[str, object]]) -> str | None:
    fragments: list[str] = []
    for item in intent_parts:
        try:
            part = ContentPartAdapter.validate_python(item)
        except Exception:
            fragment = user_prompt_content_to_text(item)
        else:
            fragment = content_parts_to_text((part,))
        normalized_fragment = str(fragment or "").strip()
        if normalized_fragment:
            fragments.append(normalized_fragment)
    if not fragments:
        return None
    return "\n\n".join(fragments)


# noinspection PyTypeHints
def _round_coordinator_message_projection(
    message: dict[str, object],
    coordinator_role_id: str | None,
) -> dict[str, object] | None:
    if coordinator_role_id is None:
        return None
    if str(message.get("role_id") or "") != coordinator_role_id:
        return None
    role = str(message.get("role") or "")
    if role != "user":
        return message
    projected_message = _tool_outcome_message_projection(
        cast(object, message.get("message"))
    )
    if projected_message is None:
        return None
    projected = dict(message)
    projected["message"] = projected_message
    return projected


# noinspection PyTypeHints
def _tool_outcome_message_projection(message: object) -> dict[str, object] | None:
    if not isinstance(message, dict):
        return None
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        return None
    outcome_parts: list[object] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_kind = str(part.get("part_kind") or "")
        if part_kind in {"tool-return", "retry-prompt"}:
            outcome_parts.append(part)
            continue
        if (
            part.get("tool_name") is not None
            and part.get("content") is not None
            and part.get("args") is None
        ):
            outcome_parts.append(part)
            continue
    if not outcome_parts:
        return None
    projected = dict(message)
    projected["parts"] = outcome_parts
    return projected


# noinspection PyTypeHints
def _parse_event_payload(payload_json: object) -> dict[str, object]:
    if not isinstance(payload_json, str) or not payload_json:
        return {}
    try:
        decoded = json.loads(payload_json)
    except ValueError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(key): value for key, value in decoded.items() if isinstance(key, str)}


# noinspection PyTypeHints
def _reconstruct_completed_output_message(
    *,
    run_id: str,
    root_task: object | None,
    coordinator_role_id: str | None,
    role_instance_map: dict[str, str],
    output_event: dict[str, str] | None,
) -> dict[str, object] | None:
    if output_event is None:
        return None
    output = str(output_event.get("output") or "").strip()
    if not output:
        return None
    task_id = ""
    if root_task is not None:
        envelope = getattr(root_task, "envelope", None)
        candidate_task_id = getattr(envelope, "task_id", None)
        if isinstance(candidate_task_id, str):
            task_id = candidate_task_id
    instance_id = ""
    if coordinator_role_id is not None:
        instance_id = str(role_instance_map.get(coordinator_role_id) or "")
    return {
        "conversation_id": "",
        "agent_role_id": coordinator_role_id or "",
        "instance_id": instance_id,
        "task_id": task_id,
        "trace_id": run_id,
        "role": "assistant",
        "role_id": coordinator_role_id or "",
        "created_at": str(output_event.get("occurred_at") or ""),
        "reconstructed": True,
        "message": {
            "parts": [
                {
                    "part_kind": "text",
                    "content": output,
                }
            ]
        },
    }


def _attach_history_markers(
    *,
    session_id: str,
    rounds: list[dict[str, object]],
    session_markers: list[dict[str, object]],
) -> None:
    if not rounds or not session_markers:
        return
    _attach_marker_before(
        rounds=rounds,
        markers=sorted(
            (
                marker
                for marker in session_markers
                if str(marker.get("marker_type") or "")
                == SessionHistoryMarkerType.CLEAR.value
            ),
            key=lambda item: str(item.get("created_at") or ""),
        ),
        field_name="clear_marker_before",
        matches_round=lambda _round, _marker: True,
        projector=_project_clear_marker,
    )
    _attach_marker_before(
        rounds=rounds,
        markers=sorted(
            (
                marker
                for marker in session_markers
                if str(marker.get("marker_type") or "")
                == SessionHistoryMarkerType.COMPACTION.value
            ),
            key=lambda item: str(item.get("created_at") or ""),
        ),
        field_name="compaction_marker_before",
        matches_round=lambda round_item, marker: _round_matches_compaction_marker(
            session_id=session_id,
            round_item=round_item,
            marker=marker,
        ),
        projector=_project_compaction_marker,
    )


def _attach_marker_before(
    *,
    rounds: list[dict[str, object]],
    markers: list[dict[str, object]],
    field_name: str,
    matches_round: Callable[[dict[str, object], dict[str, object]], bool],
    projector: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    if not markers:
        return

    marker_index = 0
    pending_marker: dict[str, object] | None = None
    for round_item in rounds:
        created_at = _parse_timestamp(round_item.get("created_at"))
        while marker_index < len(markers):
            marker = markers[marker_index]
            marker_created_at = _parse_timestamp(marker.get("created_at"))
            if (
                marker_created_at is None
                or created_at is None
                or marker_created_at > created_at
            ):
                break
            pending_marker = marker
            marker_index += 1
        if pending_marker is None or not matches_round(round_item, pending_marker):
            continue
        round_item[field_name] = projector(pending_marker)
        pending_marker = None


def _project_clear_marker(marker: dict[str, object]) -> dict[str, object]:
    return {
        "marker_id": str(marker.get("marker_id") or ""),
        "marker_type": str(marker.get("marker_type") or ""),
        "created_at": str(marker.get("created_at") or ""),
        "label": "History cleared",
    }


def _project_compaction_marker(marker: dict[str, object]) -> dict[str, object]:
    metadata = marker.get("metadata")
    strategy = ""
    if isinstance(metadata, dict):
        strategy = str(metadata.get("compaction_strategy") or "")
    return {
        "marker_id": str(marker.get("marker_id") or ""),
        "marker_type": str(marker.get("marker_type") or ""),
        "created_at": str(marker.get("created_at") or ""),
        "label": (
            "History compacted (rolling summary)"
            if strategy == "rolling_summary"
            else "History compacted"
        ),
    }


def _project_microcompact_payload(
    payload: dict[str, object],
) -> dict[str, object] | None:
    compacted_message_count = _read_non_negative_int_from_payload(
        payload, "microcompact_compacted_message_count"
    )
    compacted_part_count = _read_non_negative_int_from_payload(
        payload, "microcompact_compacted_part_count"
    )
    applied = payload.get("microcompact_applied") is True or (
        compacted_message_count > 0 or compacted_part_count > 0
    )
    if not applied:
        return None
    return {
        "applied": True,
        "estimated_tokens_before": _read_non_negative_int_from_payload(
            payload, "estimated_tokens_before_microcompact"
        ),
        "estimated_tokens_after": _read_non_negative_int_from_payload(
            payload, "estimated_tokens_after_microcompact"
        ),
        "compacted_message_count": compacted_message_count,
        "compacted_part_count": compacted_part_count,
    }


def _payload_reports_microcompact_state(payload: dict[str, object]) -> bool:
    return any(
        key in payload
        for key in (
            "microcompact_applied",
            "estimated_tokens_before_microcompact",
            "estimated_tokens_after_microcompact",
            "microcompact_compacted_message_count",
            "microcompact_compacted_part_count",
        )
    )


def _read_non_negative_int_from_payload(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return 0
        try:
            return max(0, int(raw))
        except ValueError:
            return 0
    return 0


def _round_matches_compaction_marker(
    *,
    session_id: str,
    round_item: dict[str, object],
    marker: dict[str, object],
) -> bool:
    metadata = marker.get("metadata")
    if not isinstance(metadata, dict):
        return False
    conversation_id = str(metadata.get("conversation_id") or "")
    if not conversation_id:
        return False
    primary_role_id = str(round_item.get("primary_role_id") or "")
    if primary_role_id:
        return conversation_id == build_conversation_id(session_id, primary_role_id)
    coordinator_messages = round_item.get("coordinator_messages")
    if not isinstance(coordinator_messages, list) or not coordinator_messages:
        return False
    first_message = coordinator_messages[0]
    if not isinstance(first_message, dict):
        return False
    return str(first_message.get("conversation_id") or "") == conversation_id


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None
