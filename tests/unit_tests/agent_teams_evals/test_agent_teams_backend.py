from __future__ import annotations

from pathlib import Path
from typing import Iterator

from agent_teams_evals.backends.agent_teams import AgentTeamsBackend, AgentTeamsConfig
from agent_teams_evals.workspace.base import PreparedWorkspace


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

    def create_run(
        self,
        *,
        intent: str,
        session_id: str,
        execution_mode: str,
        approval_mode: str,
    ) -> object:
        assert intent == "demo intent"
        assert session_id == "session-1"
        assert execution_mode == "ai"
        assert approval_mode == "yolo"

        class _Handle:
            run_id = "run-1"

        return _Handle()

    def stream_run_events(self, run_id: str) -> Iterator[dict[str, object]]:
        assert run_id == "run-1"
        yield {
            "event_type": "token_usage",
            "payload_json": (
                '{"input_tokens": 10, "cached_input_tokens": 2, '
                '"output_tokens": 5, "reasoning_output_tokens": 1, '
                '"requests": 3, "tool_calls": 4}'
            ),
        }
        yield {"event_type": "run_completed", "payload_json": "{}"}


def test_agent_teams_backend_emits_detailed_token_usage(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_teams_evals.backends.agent_teams.AgentTeamsClient", _FakeClient
    )
    backend = AgentTeamsBackend(
        AgentTeamsConfig(base_url="http://localhost:8000", timeout_seconds=30.0)
    )
    workspace = PreparedWorkspace(
        item_id="demo",
        repo_path=Path("."),
        base_commit="abc123",
        container_repo_path="/testbed",
    )

    events = list(backend.run("demo intent", workspace))

    assert [event.type for event in events] == ["metadata", "token_usage", "completed"]
    token_event = events[1]
    assert token_event.input_tokens == 10
    assert token_event.cached_input_tokens == 2
    assert token_event.output_tokens == 5
    assert token_event.reasoning_output_tokens == 1
    assert token_event.requests == 3
    assert token_event.tool_calls == 4
