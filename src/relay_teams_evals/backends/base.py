from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from relay_teams_evals.workspace.base import PreparedWorkspace


@dataclass
class AgentEvent:
    type: Literal[
        "metadata",
        "text_delta",
        "token_usage",
        "completed",
        "failed",
        "stopped",
        "timeout",
    ]
    text: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    requests: int = 0
    tool_calls: int = 0
    # populated by a "metadata" event emitted at run start
    run_id: str = ""
    session_id: str = ""


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = 300.0


class AgentBackend(ABC):
    @abstractmethod
    def run(
        self,
        intent: str,
        workspace: PreparedWorkspace,
        keep_workspace: bool = False,
    ) -> Iterator[AgentEvent]: ...
