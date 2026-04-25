# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from json import dumps
import sqlite3

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.assistant_errors import (
    RunCompletionReason,
    build_assistant_error_message,
    build_assistant_error_response,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_event_publisher import RunEventPublisher
from relay_teams.sessions.runs.run_models import RunEvent, RunResult
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRecord
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.workspace import build_conversation_id


class RunTerminalResultService:
    def __init__(
        self,
        *,
        session_repo: SessionRepository,
        get_runtime: Callable[[str], RunRuntimeRecord | None],
        get_agent_repo: Callable[[], AgentInstanceRepository | None],
        require_message_repo: Callable[[], MessageRepository],
        event_publisher: RunEventPublisher,
    ) -> None:
        self._session_repo = session_repo
        self._get_runtime = get_runtime
        self._get_agent_repo = get_agent_repo
        self._require_message_repo = require_message_repo
        self._event_publisher = event_publisher

    def build_completed_error_run_result(
        self,
        *,
        run_id: str,
        session_id: str,
        error_code: str,
        error_message: str,
        root_task_id: str | None = None,
        instance_id: str | None = None,
        role_id: str | None = None,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> RunResult:
        assistant_message = build_assistant_error_message(
            error_code=error_code,
            error_message=error_message,
        )
        try:
            runtime = self._get_runtime(run_id)
        except (KeyError, sqlite3.Error):
            runtime = None
        resolved_root_task_id = (
            root_task_id
            or (runtime.root_task_id if runtime is not None else None)
            or (runtime.active_task_id if runtime is not None else None)
            or run_id
        )
        resolved_instance_id = instance_id or (
            runtime.active_instance_id if runtime is not None else None
        )
        resolved_role_id = role_id or (
            runtime.active_role_id if runtime is not None else None
        )
        resolved_conversation_id = conversation_id
        resolved_workspace_id = workspace_id
        agent_repo = self._get_agent_repo()
        if resolved_instance_id and agent_repo is not None:
            try:
                instance = agent_repo.get_instance(resolved_instance_id)
            except KeyError:
                instance = None
            if instance is not None:
                resolved_conversation_id = (
                    resolved_conversation_id or instance.conversation_id
                )
                resolved_workspace_id = resolved_workspace_id or instance.workspace_id
                resolved_role_id = resolved_role_id or instance.role_id
        if resolved_conversation_id is None and resolved_role_id is not None:
            resolved_conversation_id = build_conversation_id(
                session_id, resolved_role_id
            )
        if resolved_workspace_id is None:
            try:
                resolved_workspace_id = self._session_repo.get(session_id).workspace_id
            except (KeyError, sqlite3.Error):
                resolved_workspace_id = "default"
        if resolved_conversation_id is not None:
            message_repo = self._require_message_repo()
            message_repo.prune_conversation_history_to_safe_boundary(
                resolved_conversation_id
            )
            message_repo.append(
                session_id=session_id,
                workspace_id=resolved_workspace_id,
                conversation_id=resolved_conversation_id,
                agent_role_id=resolved_role_id or "",
                instance_id=resolved_instance_id or resolved_conversation_id,
                task_id=resolved_root_task_id,
                trace_id=run_id,
                messages=[build_assistant_error_response(assistant_message)],
            )
        if resolved_instance_id is not None and resolved_role_id is not None:
            self._event_publisher.safe_publish_run_event(
                RunEvent(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=run_id,
                    task_id=resolved_root_task_id,
                    instance_id=resolved_instance_id,
                    role_id=resolved_role_id,
                    event_type=RunEventType.TEXT_DELTA,
                    payload_json=dumps(
                        {
                            "text": assistant_message,
                            "role_id": resolved_role_id,
                            "instance_id": resolved_instance_id,
                        }
                    ),
                ),
                failure_event="run.event.publish_failed",
            )
        return RunResult(
            trace_id=run_id,
            root_task_id=resolved_root_task_id,
            status="failed",
            completion_reason=RunCompletionReason.ASSISTANT_ERROR,
            error_code=error_code,
            error_message=error_message,
            output=content_parts_from_text(assistant_message),
        )

    @staticmethod
    def normalize_terminal_run_result(result: RunResult) -> RunResult:
        error_text = str(result.error_message or result.output_text or "").strip()
        output = result.output
        if not output and error_text:
            output = content_parts_from_text(error_text)
        if (
            result.status != "failed"
            and result.completion_reason != RunCompletionReason.ASSISTANT_ERROR
        ):
            if output == result.output:
                return result
            return result.model_copy(update={"output": output})
        updates: dict[str, object] = {
            "status": "failed",
            "completion_reason": RunCompletionReason.ASSISTANT_ERROR,
            "output": output,
        }
        if error_text:
            updates["error_message"] = error_text
        return result.model_copy(update=updates)
