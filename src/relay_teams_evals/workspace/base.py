from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from relay_teams_evals.models import EvalItem


class WorkspaceSetupError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        build_log_path: str | None = None,
        build_error_summary: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.build_log_path = build_log_path
        self.build_error_summary = build_error_summary


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
    # Docker mode: the repo path as a raw POSIX string as seen INSIDE the
    # container. Avoids Windows path conversion of repo_path on the host.
    container_repo_path: str | None = None
    # Terminal-Bench mode: docker compose project/file used to stop the task.
    compose_project_name: str | None = None
    compose_file_path: Path | None = None
    # Terminal-Bench mode: copied task directory used for scoring artifacts.
    terminalbench_task_path: Path | None = None


class WorkspaceSetup(ABC):
    @abstractmethod
    def prepare(self, item: EvalItem) -> PreparedWorkspace: ...

    @abstractmethod
    def cleanup(self, workspace: PreparedWorkspace) -> None: ...

    def teardown(self) -> None:
        """Called once after all items finish. Override for global cleanup."""
