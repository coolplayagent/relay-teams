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
                        in_val = data.get("input_tokens", 0)
                        out_val = data.get("output_tokens", 0)
                        in_tok = in_val if isinstance(in_val, int) else 0
                        out_tok = out_val if isinstance(out_val, int) else 0
                        _log(
                            workspace.item_id,
                            f"[event #{event_count}] token_usage: in={in_tok} out={out_tok}",
                        )
                        yield AgentEvent(
                            type="token_usage",
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                        )

                else:
                    _log(workspace.item_id, f"[event #{event_count}] {event_type}")
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
