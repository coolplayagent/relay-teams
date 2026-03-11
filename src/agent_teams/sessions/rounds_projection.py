from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import cast

from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRecord
from agent_teams.state.run_runtime_repo import RunRuntimeRepository
from agent_teams.state.task_repo import TaskRepository


def build_session_rounds(
    *,
    session_id: str,
    agent_repo: AgentInstanceRepository,
    task_repo: TaskRepository,
    approval_tickets_by_run: dict[str, list[dict[str, object]]],
    run_runtime_repo: RunRuntimeRepository,
    get_session_messages: Callable[[str], list[dict[str, object]]],
) -> list[dict[str, object]]:
    session_tasks = task_repo.list_by_session(session_id)
    session_agents = agent_repo.list_by_session(session_id)
    session_messages = get_session_messages(session_id)
    run_runtime = {
        record.run_id: record for record in run_runtime_repo.list_by_session(session_id)
    }

    agents_by_run: dict[str, list[object]] = defaultdict(list)
    instance_role_by_run: dict[str, dict[str, str]] = defaultdict(dict)
    instance_role_by_session: dict[str, str] = {}
    role_instance_by_run: dict[str, dict[str, str]] = defaultdict(dict)
    for agent in session_agents:
        agents_by_run[agent.run_id].append(agent)
        instance_role_by_run[agent.run_id][agent.instance_id] = agent.role_id
        instance_role_by_session[agent.instance_id] = agent.role_id
        role_instance_by_run[agent.run_id][agent.role_id] = agent.instance_id

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
        if task.envelope.parent_task_id is None:
            root_task_by_run[run_id] = task
            continue
        delegated_tasks_by_run[run_id].append(
            {
                "task_id": task.envelope.task_id,
                "title": task.envelope.title or task.envelope.objective[:80],
                "role_id": task.envelope.role_id,
                "status": task.status.value,
                "instance_id": task.assigned_instance_id or "",
            }
        )

    messages_by_run: dict[str, list[dict[str, object]]] = defaultdict(list)
    for message in session_messages:
        run_id = str(message.get("trace_id") or "")
        if not run_id:
            continue
        instance_id = str(message.get("instance_id") or "")
        if instance_id and not message.get("role_id"):
            role_id = instance_role_by_run.get(run_id, {}).get(instance_id)
            if not role_id:
                role_id = instance_role_by_session.get(instance_id)
            if role_id:
                message["role_id"] = role_id
        messages_by_run[run_id].append(message)

    run_ids = set(root_task_by_run.keys())
    run_ids.update(messages_by_run.keys())
    run_ids.update(delegated_tasks_by_run.keys())
    run_ids.update(run_runtime.keys())

    rounds: list[dict[str, object]] = []
    for run_id in run_ids:
        root_task = root_task_by_run.get(run_id)
        run_messages = messages_by_run.get(run_id, [])
        has_user_messages = any(
            str(message.get("role") or "") == "user" for message in run_messages
        )
        coordinator_messages = [
            message
            for message in run_messages
            if str(message.get("role_id") or "") == "coordinator_agent"
            and str(message.get("role") or "") != "user"
        ]
        created_at = _round_created_at(root_task, run_messages)
        runtime = run_runtime.get(run_id)
        pending_approvals = list(approval_tickets_by_run.get(run_id, []))
        round_item: dict[str, object] = {
            "run_id": run_id,
            "created_at": created_at,
            "intent": _round_intent(root_task, run_messages),
            "coordinator_messages": coordinator_messages,
            "has_user_messages": has_user_messages,
            "tasks": delegated_tasks_by_run.get(run_id, []),
            "instance_role_map": instance_role_by_run.get(run_id, {}),
            "role_instance_map": role_instance_by_run.get(run_id, {}),
            "task_instance_map": task_instance_map_by_run.get(run_id, {}),
            "task_status_map": task_status_map_by_run.get(run_id, {}),
            "pending_tool_approvals": pending_approvals,
            "pending_tool_approval_count": len(pending_approvals),
            "run_status": runtime.status.value if runtime is not None else None,
            "run_phase": runtime.phase.value if runtime is not None else None,
            "is_recoverable": runtime.is_recoverable if runtime is not None else False,
        }
        rounds.append(round_item)

    rounds.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
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
    root_task: object, run_messages: list[dict[str, object]]
) -> str | None:
    if root_task is not None:
        envelope = getattr(root_task, "envelope", None)
        objective = getattr(envelope, "objective", None)
        if isinstance(objective, str) and objective.strip():
            return objective
    for message in run_messages:
        if str(message.get("role") or "") != "user":
            continue
        prompt = _extract_user_prompt(cast(object, message.get("message")))
        if prompt:
            return prompt
    return None


def _extract_user_prompt(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    parts = message.get("parts")
    if not isinstance(parts, list):
        return None
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if str(part.get("part_kind") or "") != "user-prompt":
            continue
        content = str(part.get("content") or "")
        if content:
            chunks.append(content)
    if not chunks:
        return None
    return "\n".join(chunks).strip() or None
