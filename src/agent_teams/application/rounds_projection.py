from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Callable, cast

from agent_teams.core.enums import RunEventType, ScopeType
from agent_teams.core.types import JsonObject
from agent_teams.core.models import ScopeRef
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository


def collect_pending_tool_approvals(
    parsed_events: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
) -> dict[str, list[dict[str, str]]]:
    by_run_call: dict[str, dict[str, JsonObject]] = {}

    for ev, payload in parsed_events:
        run_id_value = ev.get("trace_id")
        if not isinstance(run_id_value, str) or not run_id_value:
            continue
        run_id = run_id_value
        event_type = ev.get("event_type")
        tool_call_id = payload.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            continue

        run_map = by_run_call.setdefault(run_id, {})
        if event_type == RunEventType.TOOL_APPROVAL_REQUESTED.value:
            entry = run_map.setdefault(
                tool_call_id,
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": str(payload.get("tool_name") or ""),
                    "args_preview": str(payload.get("args_preview") or ""),
                    "role_id": str(payload.get("role_id") or ""),
                    "instance_id": str(
                        payload.get("instance_id") or ev.get("instance_id") or ""
                    ),
                    "requested_at": str(ev.get("occurred_at") or ""),
                    "status": "requested",
                    "feedback": "",
                    "has_tool_result": False,
                },
            )
            entry["tool_name"] = str(payload.get("tool_name") or entry["tool_name"])
            entry["args_preview"] = str(
                payload.get("args_preview") or entry["args_preview"]
            )
            entry["role_id"] = str(payload.get("role_id") or entry["role_id"])
            entry["instance_id"] = str(
                payload.get("instance_id") or entry["instance_id"]
            )
            entry["requested_at"] = str(ev.get("occurred_at") or entry["requested_at"])
            entry["status"] = "requested"
        elif event_type == RunEventType.TOOL_APPROVAL_RESOLVED.value:
            entry = run_map.get(tool_call_id)
            if entry is None:
                continue
            action = payload.get("action")
            if isinstance(action, str) and action:
                entry["status"] = action
            feedback = payload.get("feedback")
            if isinstance(feedback, str) and feedback:
                entry["feedback"] = feedback
        elif event_type == RunEventType.TOOL_RESULT.value:
            entry = run_map.get(tool_call_id)
            if entry is not None:
                entry["has_tool_result"] = True

    result: dict[str, list[dict[str, str]]] = {}
    for run_id, run_calls in by_run_call.items():
        pending: list[dict[str, str]] = []
        for entry in run_calls.values():
            if entry.get("has_tool_result"):
                continue
            if str(entry.get("status") or "requested") != "requested":
                continue
            pending.append(
                {
                    "tool_call_id": str(entry.get("tool_call_id") or ""),
                    "tool_name": str(entry.get("tool_name") or ""),
                    "args_preview": str(entry.get("args_preview") or ""),
                    "role_id": str(entry.get("role_id") or ""),
                    "instance_id": str(entry.get("instance_id") or ""),
                    "requested_at": str(entry.get("requested_at") or ""),
                    "status": str(entry.get("status") or "requested"),
                    "feedback": str(entry.get("feedback") or ""),
                }
            )
        pending.sort(key=lambda item: item.get("requested_at") or "")
        if pending:
            result[run_id] = pending
    return result


def collect_pending_stream_snapshots(
    parsed_events: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
    session_messages: Sequence[Mapping[str, object]],
    by_run_instance_role: dict[str, dict[str, str]],
) -> dict[str, JsonObject]:
    persisted_by_run_actor: dict[str, dict[str, str]] = {}
    for msg in session_messages:
        if str(msg.get("role") or "") == "user":
            continue
        run_id = str(msg.get("trace_id") or "")
        if not run_id:
            continue
        instance_id = str(msg.get("instance_id") or "")
        role_id = str(
            msg.get("role_id")
            or by_run_instance_role.get(run_id, {}).get(instance_id)
            or ""
        )
        text = _extract_text_from_message(msg.get("message"))
        if not text:
            continue
        actor_map = persisted_by_run_actor.setdefault(run_id, {})
        if instance_id:
            actor_map[instance_id] = actor_map.get(instance_id, "") + text
        if role_id:
            role_key = f"role:{role_id}"
            actor_map[role_key] = actor_map.get(role_key, "") + text

    active_steps: dict[tuple[str, str], dict[str, str]] = {}
    terminal_events = {
        RunEventType.RUN_COMPLETED.value,
        RunEventType.RUN_FAILED.value,
        RunEventType.RUN_STOPPED.value,
    }

    for ev, payload in parsed_events:
        run_id = str(ev.get("trace_id") or "")
        if not run_id:
            continue
        event_type = str(ev.get("event_type") or "")
        if not event_type:
            continue
        safe_payload = payload if isinstance(payload, dict) else {}
        event_instance_id = str(ev.get("instance_id") or "")
        instance_id = str(safe_payload.get("instance_id") or event_instance_id or "")
        role_id = str(
            safe_payload.get("role_id")
            or by_run_instance_role.get(run_id, {}).get(instance_id)
            or ""
        )
        actor_key = instance_id or (f"role:{role_id}" if role_id else "coordinator")
        state_key = (run_id, actor_key)

        if event_type == RunEventType.MODEL_STEP_STARTED.value:
            active_steps[state_key] = {
                "run_id": run_id,
                "actor_key": actor_key,
                "instance_id": instance_id,
                "role_id": role_id,
                "text": "",
            }
            continue

        if event_type == RunEventType.TEXT_DELTA.value:
            chunk = safe_payload.get("text")
            if not isinstance(chunk, str) or not chunk:
                continue
            state = active_steps.setdefault(
                state_key,
                {
                    "run_id": run_id,
                    "actor_key": actor_key,
                    "instance_id": instance_id,
                    "role_id": role_id,
                    "text": "",
                },
            )
            state["text"] = f"{state['text']}{chunk}"
            if role_id and not state.get("role_id"):
                state["role_id"] = role_id
            if instance_id and not state.get("instance_id"):
                state["instance_id"] = instance_id
            continue

        if event_type == RunEventType.MODEL_STEP_FINISHED.value:
            _ = active_steps.pop(state_key, None)
            continue

        if event_type in terminal_events:
            stale_keys = [key for key in active_steps.keys() if key[0] == run_id]
            for key in stale_keys:
                _ = active_steps.pop(key, None)

    snapshots: dict[str, JsonObject] = {}
    for state in active_steps.values():
        run_id = str(state.get("run_id") or "")
        if not run_id:
            continue
        pending_text = str(state.get("text") or "")
        if not pending_text.strip():
            continue

        actor_key = str(state.get("actor_key") or "")
        role_id = str(state.get("role_id") or "")
        instance_id = str(state.get("instance_id") or "")
        persisted_map = persisted_by_run_actor.get(run_id, {})
        persisted_text = persisted_map.get(actor_key, "")
        if not persisted_text and role_id:
            persisted_text = persisted_map.get(f"role:{role_id}", "")

        delta = _subtract_persisted_text(pending_text, persisted_text)
        if not delta:
            continue

        entry = snapshots.setdefault(
            run_id,
            {
                "coordinator_text": "",
                "coordinator_instance_id": "",
                "by_instance": {},
            },
        )

        is_coordinator = role_id == "coordinator_agent" or (
            not instance_id and actor_key in {"coordinator", "role:coordinator_agent"}
        )
        if is_coordinator:
            entry["coordinator_text"] = delta
            if instance_id:
                entry["coordinator_instance_id"] = instance_id
            continue
        if instance_id:
            by_instance = entry.get("by_instance")
            if not isinstance(by_instance, dict):
                by_instance = {}
                entry["by_instance"] = by_instance
            by_instance[instance_id] = delta
            continue
        entry["coordinator_text"] = delta

    return snapshots


def _extract_text_from_message(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("part_kind") != "text":
            continue
        content = part.get("content")
        if isinstance(content, str) and content:
            chunks.append(content)
    return "".join(chunks)


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _subtract_persisted_text(pending_text: str, persisted_text: str) -> str:
    pending = str(pending_text or "")
    if not pending.strip():
        return ""
    persisted = str(persisted_text or "")
    if not persisted.strip():
        return pending
    if pending.startswith(persisted):
        delta = pending[len(persisted) :]
        return delta if delta.strip() else ""

    pending_norm = _normalize_text(pending)
    persisted_norm = _normalize_text(persisted)
    if not pending_norm:
        return ""
    if pending_norm == persisted_norm:
        return ""
    if pending_norm in persisted_norm:
        return ""

    max_overlap = min(len(pending), len(persisted))
    overlap = 0
    for length in range(max_overlap, 0, -1):
        if persisted[-length:] == pending[:length]:
            overlap = length
            break
    delta = pending[overlap:]
    return delta if delta.strip() else ""


def build_session_rounds(
    *,
    session_id: str,
    event_log: EventLog,
    agent_repo: AgentInstanceRepository,
    task_repo: TaskRepository,
    shared_store: SharedStore,
    get_session_messages: Callable[[str], list[dict[str, object]]],
) -> list[dict[str, object]]:
    events = event_log.list_by_session(session_id)
    session_tasks = task_repo.list_by_session(session_id)
    parsed_events: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    for ev in events:
        try:
            payload_json: str = cast(str, ev["payload_json"])
            payload_obj: object = json.loads(payload_json)
            if isinstance(payload_obj, dict):
                payload = payload_obj
            else:
                payload = {}
        except Exception:
            payload = {}
        parsed_events.append((ev, payload))

    rounds_map: dict[str, dict[str, object]] = {}
    by_run_instance_role: dict[str, dict[str, str]] = {}
    by_run_role_instance: dict[str, dict[str, str]] = {}
    by_run_task_instance: dict[str, dict[str, str]] = {}

    for ev, payload in parsed_events:
        run_id_value = ev.get("trace_id")
        if not isinstance(run_id_value, str) or not run_id_value:
            continue
        run_id = run_id_value
        ev_instance = ev.get("instance_id") or payload.get("instance_id")
        ev_role = payload.get("role_id")
        if isinstance(ev_instance, str) and isinstance(ev_role, str):
            by_run_instance_role.setdefault(run_id, {})[ev_instance] = ev_role
            # Keep the latest seen instance for each role in the run timeline.
            by_run_role_instance.setdefault(run_id, {})[ev_role] = ev_instance

    for rec in agent_repo.list_by_session(session_id):
        run_map = by_run_instance_role.setdefault(rec.run_id, {})
        _ = run_map.setdefault(rec.instance_id, rec.role_id)
        role_map = by_run_role_instance.setdefault(rec.run_id, {})
        _ = role_map.setdefault(rec.role_id, rec.instance_id)

    for task in session_tasks:
        run_id = task.envelope.trace_id
        assigned_instance_id = task.assigned_instance_id
        if not assigned_instance_id:
            continue
        task_map = by_run_task_instance.setdefault(run_id, {})
        task_map[task.envelope.task_id] = assigned_instance_id

    messages = get_session_messages(session_id)
    for msg in messages:
        run_id = str(msg.get("trace_id") or "")
        instance_id = str(msg.get("instance_id") or "")
        if not run_id or not instance_id:
            continue
        role_id = by_run_instance_role.get(run_id, {}).get(instance_id)
        if role_id:
            msg["role_id"] = role_id

    pending_by_run = collect_pending_tool_approvals(parsed_events)
    pending_streams_by_run = collect_pending_stream_snapshots(
        parsed_events,
        messages,
        by_run_instance_role,
    )

    for ev in events:
        run_id_value = ev.get("trace_id")
        if not isinstance(run_id_value, str) or not run_id_value:
            continue
        run_id = run_id_value
        if run_id not in rounds_map:
            rounds_map[run_id] = {
                "run_id": run_id,
                "created_at": ev["occurred_at"],
                "intent": None,
                "coordinator_messages": [],
                "pending_tool_approvals": pending_by_run.get(run_id, []),
                "pending_streams": pending_streams_by_run.get(
                    run_id,
                    {
                        "coordinator_text": "",
                        "coordinator_instance_id": "",
                        "by_instance": {},
                    },
                ),
                "workflows": [],
                "instance_role_map": by_run_instance_role.get(run_id, {}),
                "role_instance_map": by_run_role_instance.get(run_id, {}),
                "task_instance_map": by_run_task_instance.get(run_id, {}),
            }
        occurred_at: str = cast(str, ev["occurred_at"])
        created_at: str = cast(str, rounds_map[run_id]["created_at"])
        if occurred_at < created_at:
            rounds_map[run_id]["created_at"] = ev["occurred_at"]

    for msg in messages:
        run_id = msg.get("trace_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        round_data = rounds_map.get(run_id)
        if round_data is None:
            continue
        instance_id = msg.get("instance_id")
        instance_key = instance_id if isinstance(instance_id, str) else ""
        role_id = msg.get("role_id") or by_run_instance_role.get(run_id, {}).get(
            instance_key
        )
        msg["role_id"] = role_id

        if msg.get("role") == "user":
            content = msg.get("message", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []
            for pt in parts:
                if not isinstance(pt, dict):
                    continue
                if pt.get("part_kind") == "user-prompt" and not round_data["intent"]:
                    round_data["intent"] = pt.get("content", "")
                    break

        if role_id == "coordinator_agent":
            coordinator_messages = round_data.get("coordinator_messages")
            if isinstance(coordinator_messages, list):
                coordinator_messages.append(msg)

    for task in session_tasks:
        run_id = task.envelope.trace_id
        round_data = rounds_map.get(run_id)
        if round_data is None:
            continue
        scope = ScopeRef(scope_type=ScopeType.TASK, scope_id=task.envelope.task_id)
        wf_str = shared_store.get_state(scope, "workflow_graph")
        if wf_str:
            workflows = round_data.get("workflows")
            if isinstance(workflows, list):
                workflows.append(json.loads(wf_str))

    return sorted(
        list(rounds_map.values()),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )


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
