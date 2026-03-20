from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict

from agent_teams_evals.backends.base import AgentConfig


class AgentTeamsConfig(AgentConfig):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://127.0.0.1:8000"
    execution_mode: str = "ai"
    yolo: bool = True
    # Docker mode: mount this directory as ~/.config/agent-teams inside the container.
    # Controls which model, role and system prompt the agent uses.
    # None = use whatever config is already present in the container.
    config_dir: Path | None = None
