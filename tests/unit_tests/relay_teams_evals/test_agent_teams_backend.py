from __future__ import annotations

from pathlib import Path
from typing import Iterator

from relay_teams_evals.backends.agent_teams import AgentTeamsBackend, AgentTeamsConfig
from relay_teams_evals.workspace.base import PreparedWorkspace


class _FakeClient:
    def __init__(
        self,
        *,
        base_url: str,
        stream_timeout_seconds: float,
    ) -> None:
        assert base_url == "http://localhost:8000"
        assert stream_timeout_seconds == 30.0

    def delete_workspace(self, workspace_id: str) -> None:
        assert workspace_id == "eval-demo"

    def create_workspace(self, *, workspace_id: str, root_path: str) -> None:
        assert workspace_id == "eval-demo"
        assert root_path == "/testbed"

    def create_session(self, *, workspace_id: str) -> dict[str, str]:
        assert workspace_id == "eval-demo"
        return {"session_id": "session-1"}

    def update_session_topology(
        self,
        session_id: str,
        *,
        session_mode: str,
        orchestration_preset_id: str | None = None,
    ) -> dict[str, object]:
        raise AssertionError(
            "update_session_topology should not be called in normal mode"
        )

    def create_run(
        self,
        *,
        input: str,
        session_id: str,
        execution_mode: str,
        yolo: bool,
    ) -> object:
        assert input == "demo intent"
        assert session_id == "session-1"
        assert execution_mode == "ai"
        assert yolo is True

        class _Handle:
            run_id = "run-1"

        return _Handle()

    def stream_run_events(self, run_id: str) -> Iterator[dict[str, object]]:
        assert run_id == "run-1"
        yield {
            "event_type": "run_started",
            "payload_json": '{"session_id": "session-1"}',
        }
        yield {
            "event_type": "model_step_started",
            "payload_json": (
                '{"role_id": "coordinator", "instance_id": "coord_12345678"}'
            ),
        }
        yield {
            "event_type": "llm_retry_scheduled",
            "payload_json": (
                '{"attempt_number": 2, "total_attempts": 4, '
                '"retry_in_ms": 1500, "status_code": 429, '
                '"error_code": "rate_limit", '
                '"error_message": "Provider rate limit reached"}'
            ),
        }
        yield {
            "event_type": "tool_call",
            "payload_json": (
                '{"tool_name": "orch_dispatch_task", "tool_call_id": "abc123456789", '
                '"role_id": "crafter", "instance_id": "subagent_01"}'
            ),
        }
        yield {
            "event_type": "tool_result",
            "payload_json": (
                '{"tool_name": "orch_dispatch_task", "tool_call_id": "abc123456789", '
                '"result": {"message": "Task dispatched successfully"}, '
                '"error": false}'
            ),
        }
        yield {
            "event_type": "tool_result",
            "payload_json": (
                '{"tool_name": "apply_patch", "tool_call_id": "zzz11112222", '
                '"result": {"error": "Patch context did not match"}, '
                '"error": true}'
            ),
        }
        yield {
            "event_type": "tool_input_validation_failed",
            "payload_json": (
                '{"tool_name": "apply_patch", "tool_call_id": "def987654321", '
                '"reason": "Patch hunk is malformed"}'
            ),
        }
        yield {
            "event_type": "tool_approval_requested",
            "payload_json": (
                '{"tool_name": "shell_command", "tool_call_id": "ghi555555555", '
                '"risk_level": "high"}'
            ),
        }
        yield {
            "event_type": "tool_approval_resolved",
            "payload_json": (
                '{"tool_name": "shell_command", "tool_call_id": "ghi555555555", '
                '"action": "approve"}'
            ),
        }
        yield {
            "event_type": "thinking_started",
            "payload_json": (
                '{"part_index": 0, "role_id": "coordinator", '
                '"instance_id": "coord_12345678"}'
            ),
        }
        yield {
            "event_type": "thinking_delta",
            "payload_json": (
                '{"part_index": 0, "text": "maintains", '
                '"role_id": "coordinator", "instance_id": "coord_12345678"}'
            ),
        }
        yield {
            "event_type": "thinking_finished",
            "payload_json": (
                '{"part_index": 0, "role_id": "coordinator", '
                '"instance_id": "coord_12345678"}'
            ),
        }
        yield {
            "event_type": "injection_enqueued",
            "payload_json": (
                '{"recipient_instance_id": "subagent_01", "source": "user", '
                '"sender_role_id": "coordinator", '
                '"content": "Please retry with more context"}'
            ),
        }
        yield {
            "event_type": "injection_applied",
            "payload_json": (
                '{"source": "user", "content": "Please retry with more context"}'
            ),
        }
        yield {
            "event_type": "notification_requested",
            "payload_json": (
                '{"notification_type": "run_failed", "title": "Run Failed", '
                '"body": "Run run-1 failed"}'
            ),
        }
        yield {
            "event_type": "subagent_stopped",
            "payload_json": (
                '{"instance_id": "subagent_01", "role_id": "crafter", '
                '"task_id": "task_12345678", "reason": "stopped_by_user"}'
            ),
        }
        yield {
            "event_type": "subagent_resumed",
            "payload_json": (
                '{"instance_id": "subagent_01", "role_id": "crafter", '
                '"task_id": "task_12345678"}'
            ),
        }
        yield {
            "event_type": "awaiting_manual_action",
            "payload_json": '{"root_task_id": "root_12345678"}',
        }
        yield {
            "event_type": "token_usage",
            "payload_json": (
                '{"input_tokens": 10, "cached_input_tokens": 2, '
                '"output_tokens": 5, "reasoning_output_tokens": 1, '
                '"requests": 3, "tool_calls": 4}'
            ),
        }
        yield {
            "event_type": "model_step_finished",
            "payload_json": (
                '{"role_id": "coordinator", "instance_id": "coord_12345678"}'
            ),
        }
        yield {
            "event_type": "run_completed",
            "payload_json": (
                '{"trace_id": "run-1", "root_task_id": "root_12345678", '
                '"status": "completed", "output": "Task finished successfully"}'
            ),
        }


def test_agent_teams_backend_emits_detailed_runtime_logs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "relay_teams_evals.backends.agent_teams.AgentTeamsClient", _FakeClient
    )
    backend = AgentTeamsBackend(
        AgentTeamsConfig(
            base_url="http://localhost:8000",
            timeout_seconds=30.0,
            yolo=True,
        )
    )
    workspace = PreparedWorkspace(
        item_id="demo",
        repo_path=Path("."),
        base_commit="abc123",
        container_repo_path="/testbed",
    )

    events = list(backend.run("demo intent", workspace))
    out = capsys.readouterr().out

    assert [event.type for event in events] == ["metadata", "token_usage", "completed"]
    token_event = events[1]
    assert token_event.input_tokens == 10
    assert token_event.cached_input_tokens == 2
    assert token_event.output_tokens == 5
    assert token_event.reasoning_output_tokens == 1
    assert token_event.requests == 3
    assert token_event.tool_calls == 4
    assert "[event #1] run_started: session=session-" in out
    assert (
        "[event #2] model_step_started: role=coordinator instance=coord_12345678" in out
    )
    assert (
        "[event #3] llm_retry_scheduled: attempt=2/4 retry_in_ms=1500 "
        "status_code=429 error_code=rate_limit message=Provider rate limit reached"
        in out
    )
    assert (
        "[event #4] tool_call: tool=orch_dispatch_task id=abc12345 "
        "role=crafter instance=subagent_01" in out
    )
    assert (
        "[event #5] tool_result: tool=orch_dispatch_task id=abc12345 "
        "status=ok summary=Task dispatched successfully" in out
    )
    assert (
        "[event #6] tool_result: tool=apply_patch id=zzz11112 "
        "status=error summary=Patch context did not match" in out
    )
    assert (
        "[event #7] tool_input_validation_failed: tool=apply_patch "
        "id=def98765 reason=Patch hunk is malformed" in out
    )
    assert (
        "[event #8] tool_approval_requested: tool=shell_command "
        "id=ghi55555 risk=high" in out
    )
    assert (
        "[event #9] tool_approval_resolved: tool=shell_command "
        "id=ghi55555 action=approve" in out
    )
    assert (
        "[event #10] thinking_started: part=0 role=coordinator "
        "instance=coord_12345678" in out
    )
    assert "[event #11] thinking_delta" not in out
    assert (
        "[event #12] thinking_finished: part=0 role=coordinator "
        "instance=coord_12345678" in out
    )
    assert (
        "[event #13] injection_enqueued: source=user recipient=subagent "
        "sender_role=coordinator content=Please retry with more context" in out
    )
    assert (
        "[event #14] injection_applied: source=user "
        "content=Please retry with more context" in out
    )
    assert "[event #15] notification_requested: type=run_failed title=Run Failed" in out
    assert (
        "[event #16] subagent_stopped: instance=subagent role=crafter "
        "task=task_123 reason=stopped_by_user" in out
    )
    assert (
        "[event #17] subagent_resumed: instance=subagent role=crafter "
        "task=task_123" in out
    )
    assert "[event #18] awaiting_manual_action: root_task=root_123" in out
    assert (
        "[event #19] token_usage: input=10 cached=2 output=5 "
        "reasoning=1 requests=3 tool_calls=4" in out
    )
    assert (
        "[event #20] model_step_finished: role=coordinator "
        "instance=coord_12345678" in out
    )
    assert (
        "[event #21] run_completed: status=completed "
        "root_task=root_123 output=Task finished successfully" in out
    )


class _FakeOrchestrationClient(_FakeClient):
    def update_session_topology(
        self,
        session_id: str,
        *,
        session_mode: str,
        orchestration_preset_id: str | None = None,
    ) -> dict[str, object]:
        assert session_id == "session-1"
        assert session_mode == "orchestration"
        assert orchestration_preset_id == "default"
        return {
            "session_id": session_id,
            "session_mode": session_mode,
            "orchestration_preset_id": orchestration_preset_id,
        }


def test_agent_teams_backend_configures_orchestration_session(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "relay_teams_evals.backends.agent_teams.AgentTeamsClient",
        _FakeOrchestrationClient,
    )
    backend = AgentTeamsBackend(
        AgentTeamsConfig(
            base_url="http://localhost:8000",
            timeout_seconds=30.0,
            session_mode="orchestration",
            orchestration_preset_id="default",
            yolo=True,
        )
    )
    workspace = PreparedWorkspace(
        item_id="demo",
        repo_path=Path("."),
        base_commit="abc123",
        container_repo_path="/testbed",
    )

    events = list(backend.run("demo intent", workspace))
    out = capsys.readouterr().out

    assert [event.type for event in events] == ["metadata", "token_usage", "completed"]
    assert "configuring session mode: orchestration preset=default" in out
