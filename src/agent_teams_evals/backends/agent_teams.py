from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import typer
from pydantic import ConfigDict

from agent_teams.interfaces.sdk.client import AgentTeamsClient
from agent_teams_evals.backends.base import AgentBackend, AgentConfig, AgentEvent
from agent_teams_evals.workspace.base import PreparedWorkspace

_TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "run_stopped"})


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


def _event_int(data: dict[str, object], key: str) -> int:
    value = data.get(key, 0)
    return value if isinstance(value, int) else 0


def _event_str(data: dict[str, object], key: str) -> str:
    value = data.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _short_id(value: str, *, length: int = 8) -> str:
    return value[:length] if value else ""


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, *, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_scalar_preview(value: object) -> str:
    if isinstance(value, str):
        return _truncate(_single_line(value))
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def _tool_result_summary(data: dict[str, object]) -> str:
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("message", "summary", "status", "error"):
            value = result.get(key)
            preview = _format_scalar_preview(value)
            if preview:
                return preview
        return ""
    return _format_scalar_preview(result)


def _common_actor_suffix(data: dict[str, object]) -> str:
    role_id = _event_str(data, "role_id")
    instance_id = _event_str(data, "instance_id")
    parts: list[str] = []
    if role_id:
        parts.append(f"role={role_id}")
    if instance_id:
        parts.append(f"instance={instance_id}")
    return (" " + " ".join(parts)) if parts else ""


def _log_run_event(
    item_id: str, event_count: int, event_type: str, data: object
) -> None:
    if event_type in {"thinking_delta", "text_delta"}:
        return

    if not isinstance(data, dict):
        _log(item_id, f"[event #{event_count}] {event_type}")
        return

    if event_type == "tool_call":
        tool_name = _event_str(data, "tool_name") or "unknown"
        tool_call_id = _short_id(_event_str(data, "tool_call_id"))
        _log(
            item_id,
            f"[event #{event_count}] tool_call: tool={tool_name} "
            f"id={tool_call_id or '-'}{_common_actor_suffix(data)}",
        )
        return

    if event_type == "tool_result":
        tool_name = _event_str(data, "tool_name") or "unknown"
        tool_call_id = _short_id(_event_str(data, "tool_call_id"))
        status = "error" if bool(data.get("error")) else "ok"
        summary = _tool_result_summary(data)
        message = (
            f"[event #{event_count}] tool_result: tool={tool_name} "
            f"id={tool_call_id or '-'} status={status}"
        )
        if summary:
            message += f" summary={summary}"
        _log(item_id, message)
        return

    if event_type == "tool_input_validation_failed":
        tool_name = _event_str(data, "tool_name") or "unknown"
        tool_call_id = _short_id(_event_str(data, "tool_call_id"))
        reason = _truncate(_single_line(_event_str(data, "reason")))
        _log(
            item_id,
            f"[event #{event_count}] tool_input_validation_failed: "
            f"tool={tool_name} id={tool_call_id or '-'} "
            f"reason={reason or 'validation failed'}",
        )
        return

    if event_type == "tool_approval_requested":
        tool_name = _event_str(data, "tool_name") or "unknown"
        tool_call_id = _short_id(_event_str(data, "tool_call_id"))
        risk_level = _event_str(data, "risk_level")
        message = (
            f"[event #{event_count}] tool_approval_requested: tool={tool_name} "
            f"id={tool_call_id or '-'}"
        )
        if risk_level:
            message += f" risk={risk_level}"
        _log(item_id, message)
        return

    if event_type == "tool_approval_resolved":
        tool_name = _event_str(data, "tool_name") or "unknown"
        tool_call_id = _short_id(_event_str(data, "tool_call_id"))
        action = _event_str(data, "action") or "unknown"
        _log(
            item_id,
            f"[event #{event_count}] tool_approval_resolved: "
            f"tool={tool_name} id={tool_call_id or '-'} action={action}",
        )
        return

    if event_type == "run_started":
        session_id = _short_id(_event_str(data, "session_id"))
        _log(
            item_id,
            f"[event #{event_count}] run_started: session={session_id or '-'}",
        )
        return

    if event_type == "run_resumed":
        session_id = _short_id(_event_str(data, "session_id"))
        reason = _event_str(data, "reason") or "resume"
        _log(
            item_id,
            f"[event #{event_count}] run_resumed: session={session_id or '-'} "
            f"reason={reason}",
        )
        return

    if event_type == "model_step_started":
        _log(
            item_id,
            f"[event #{event_count}] model_step_started:{_common_actor_suffix(data)}",
        )
        return

    if event_type == "model_step_finished":
        _log(
            item_id,
            f"[event #{event_count}] model_step_finished:{_common_actor_suffix(data)}",
        )
        return

    if event_type == "thinking_started":
        part_index = _event_int(data, "part_index")
        _log(
            item_id,
            f"[event #{event_count}] thinking_started: part={part_index}"
            f"{_common_actor_suffix(data)}",
        )
        return

    if event_type == "thinking_finished":
        part_index = _event_int(data, "part_index")
        _log(
            item_id,
            f"[event #{event_count}] thinking_finished: part={part_index}"
            f"{_common_actor_suffix(data)}",
        )
        return

    if event_type == "llm_retry_scheduled":
        attempt = _event_int(data, "attempt_number") or _event_int(
            data, "next_attempt_number"
        )
        total_attempts = _event_int(data, "total_attempts")
        retry_in_ms = _event_int(data, "retry_in_ms")
        status_code = _event_int(data, "status_code")
        error_code = _event_str(data, "error_code")
        error_message = _truncate(_single_line(_event_str(data, "error_message")))
        message = (
            f"[event #{event_count}] llm_retry_scheduled: attempt={attempt}"
            f"/{total_attempts} retry_in_ms={retry_in_ms}"
        )
        if status_code:
            message += f" status_code={status_code}"
        if error_code:
            message += f" error_code={error_code}"
        if error_message:
            message += f" message={error_message}"
        _log(item_id, message)
        return

    if event_type == "llm_retry_exhausted":
        attempt = _event_int(data, "attempt_number")
        total_attempts = _event_int(data, "total_attempts")
        status_code = _event_int(data, "status_code")
        error_code = _event_str(data, "error_code")
        error_message = _truncate(_single_line(_event_str(data, "error_message")))
        message = (
            f"[event #{event_count}] llm_retry_exhausted: attempt={attempt}"
            f"/{total_attempts}"
        )
        if status_code:
            message += f" status_code={status_code}"
        if error_code:
            message += f" error_code={error_code}"
        if error_message:
            message += f" message={error_message}"
        _log(item_id, message)
        return

    if event_type == "injection_enqueued":
        source = _event_str(data, "source") or "unknown"
        recipient = _short_id(_event_str(data, "recipient_instance_id"))
        sender_role = _event_str(data, "sender_role_id")
        content = _truncate(_single_line(_event_str(data, "content")), limit=80)
        message = (
            f"[event #{event_count}] injection_enqueued: source={source} "
            f"recipient={recipient or '-'}"
        )
        if sender_role:
            message += f" sender_role={sender_role}"
        if content:
            message += f" content={content}"
        _log(item_id, message)
        return

    if event_type == "injection_applied":
        source = _event_str(data, "source") or "unknown"
        content = _truncate(_single_line(_event_str(data, "content")), limit=80)
        message = f"[event #{event_count}] injection_applied: source={source}"
        if content:
            message += f" content={content}"
        _log(item_id, message)
        return

    if event_type == "notification_requested":
        notification_type = _event_str(data, "notification_type") or "unknown"
        title = _truncate(_single_line(_event_str(data, "title")), limit=80)
        _log(
            item_id,
            f"[event #{event_count}] notification_requested: "
            f"type={notification_type} title={title or '-'}",
        )
        return

    if event_type == "subagent_stopped":
        instance_id = _short_id(_event_str(data, "instance_id"))
        role_id = _event_str(data, "role_id")
        task_id = _short_id(_event_str(data, "task_id"))
        reason = _truncate(_single_line(_event_str(data, "reason")))
        message = (
            f"[event #{event_count}] subagent_stopped: instance={instance_id or '-'}"
        )
        if role_id:
            message += f" role={role_id}"
        if task_id:
            message += f" task={task_id}"
        if reason:
            message += f" reason={reason}"
        _log(item_id, message)
        return

    if event_type == "subagent_resumed":
        instance_id = _short_id(_event_str(data, "instance_id"))
        role_id = _event_str(data, "role_id")
        task_id = _short_id(_event_str(data, "task_id"))
        message = (
            f"[event #{event_count}] subagent_resumed: instance={instance_id or '-'}"
        )
        if role_id:
            message += f" role={role_id}"
        if task_id:
            message += f" task={task_id}"
        _log(item_id, message)
        return

    if event_type == "run_stopped":
        reason = _event_str(data, "reason") or "unknown"
        _log(item_id, f"[event #{event_count}] run_stopped: reason={reason}")
        return

    if event_type == "run_completed":
        status = _event_str(data, "status") or "completed"
        root_task_id = _short_id(_event_str(data, "root_task_id"))
        output = _truncate(_single_line(_event_str(data, "output")), limit=100)
        message = f"[event #{event_count}] run_completed: status={status}"
        if root_task_id:
            message += f" root_task={root_task_id}"
        if output:
            message += f" output={output}"
        _log(item_id, message)
        return

    if event_type == "run_failed":
        error = _truncate(
            _single_line(_event_str(data, "error") or _event_str(data, "output")),
            limit=100,
        )
        status = _event_str(data, "status") or "failed"
        message = f"[event #{event_count}] run_failed: status={status}"
        if error:
            message += f" error={error}"
        _log(item_id, message)
        return

    if event_type == "awaiting_manual_action":
        root_task_id = _short_id(_event_str(data, "root_task_id"))
        _log(
            item_id,
            f"[event #{event_count}] awaiting_manual_action: "
            f"root_task={root_task_id or '-'}",
        )
        return

    _log(item_id, f"[event #{event_count}] {event_type}")


def _try_delete_workspace(client: AgentTeamsClient, workspace_id: str) -> None:
    try:
        client.delete_workspace(workspace_id)
    except Exception:
        pass


class AgentTeamsConfig(AgentConfig):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://127.0.0.1:8000"
    execution_mode: str = "ai"
    approval_mode: str = "yolo"
    # Docker mode: mount this directory as ~/.config/agent-teams inside the container.
    # Controls which model, role and system prompt the agent uses.
    # None = use whatever config is already present in the container.
    config_dir: Path | None = None


class AgentTeamsBackend(AgentBackend):
    def __init__(self, config: AgentTeamsConfig) -> None:
        self._config = config

    def run(
        self,
        intent: str,
        workspace: PreparedWorkspace,
        keep_workspace: bool = False,
    ) -> Iterator[AgentEvent]:
        base_url = workspace.agent_base_url or self._config.base_url
        client = AgentTeamsClient(
            base_url=base_url,
            stream_timeout_seconds=self._config.timeout_seconds,
        )

        workspace_id = f"eval-{workspace.item_id}"
        _try_delete_workspace(client, workspace_id)
        _log(workspace.item_id, f"registering workspace {workspace_id!r} ...")
        root_path = workspace.container_repo_path or str(workspace.repo_path.resolve())
        client.create_workspace(
            workspace_id=workspace_id,
            root_path=root_path,
        )

        try:
            _log(workspace.item_id, "creating session ...")
            session_data = client.create_session(workspace_id=workspace_id)
            session_id = str(session_data.get("session_id", ""))
            _log(workspace.item_id, f"session: {session_id}")

            _log(workspace.item_id, "creating run ...")
            run_handle = client.create_run(
                intent=intent,
                session_id=session_id,
                execution_mode=self._config.execution_mode,
                approval_mode=self._config.approval_mode,
            )
            run_id = run_handle.run_id
            _log(workspace.item_id, f"run: {run_id}")

            yield AgentEvent(type="metadata", run_id=run_id, session_id=session_id)

            _log(workspace.item_id, "streaming events ...")
            event_count = 0
            for raw_event in client.stream_run_events(run_id):
                event_count += 1
                event_type = raw_event.get("event_type")
                payload_json = raw_event.get("payload_json", "{}")
                data: object = (
                    json.loads(payload_json) if isinstance(payload_json, str) else {}
                )

                if event_type == "text_delta":
                    if isinstance(data, dict):
                        text = data.get("text", "")
                        if isinstance(text, str) and text:
                            yield AgentEvent(type="text_delta", text=text)

                elif event_type == "token_usage":
                    if isinstance(data, dict):
                        in_tok = _event_int(data, "input_tokens")
                        cached_in_tok = _event_int(data, "cached_input_tokens")
                        out_tok = _event_int(data, "output_tokens")
                        reasoning_out_tok = _event_int(data, "reasoning_output_tokens")
                        requests = _event_int(data, "requests")
                        tool_calls = _event_int(data, "tool_calls")
                        _log(
                            workspace.item_id,
                            f"[event #{event_count}] token_usage: "
                            f"input={in_tok} cached={cached_in_tok} "
                            f"output={out_tok} reasoning={reasoning_out_tok} "
                            f"requests={requests} tool_calls={tool_calls}",
                        )
                        yield AgentEvent(
                            type="token_usage",
                            input_tokens=in_tok,
                            cached_input_tokens=cached_in_tok,
                            output_tokens=out_tok,
                            reasoning_output_tokens=reasoning_out_tok,
                            requests=requests,
                            tool_calls=tool_calls,
                        )

                else:
                    _log_run_event(
                        workspace.item_id,
                        event_count,
                        str(event_type),
                        data,
                    )
                    if event_type == "run_completed":
                        yield AgentEvent(type="completed")
                    elif event_type == "run_failed":
                        yield AgentEvent(type="failed")
                    elif event_type == "run_stopped":
                        yield AgentEvent(type="stopped")

                if event_type in _TERMINAL_EVENTS:
                    break

        finally:
            if not keep_workspace:
                _try_delete_workspace(client, workspace_id)
