from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams_evals.models import EvalItem


class PreparedWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    repo_path: Path
    base_commit: str
    # Docker mode: ID of the running container for this item.
    container_id: str | None = None
    # Docker mode: base URL of the agent-teams server inside the container,
    # e.g. "http://localhost:8023". Overrides backend config.base_url when set.
    agent_base_url: str | None = None


class WorkspaceSetup(ABC):
    @abstractmethod
    def prepare(self, item: EvalItem) -> PreparedWorkspace: ...

    @abstractmethod
    def cleanup(self, workspace: PreparedWorkspace) -> None: ...
