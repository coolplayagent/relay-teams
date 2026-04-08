from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from relay_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage

if TYPE_CHECKING:
    from relay_teams_evals.workspace.base import PreparedWorkspace


class Scorer(ABC):
    @abstractmethod
    def score(
        self,
        *,
        item: EvalItem,
        run_id: str,
        session_id: str,
        outcome: RunOutcome,
        agent_output: str,
        generated_patch: str,
        raw_generated_patch: str,
        filtered_generated_files: tuple[str, ...],
        token_usage: TokenUsage,
        duration_seconds: float,
        workspace: PreparedWorkspace | None = None,
        error: str | None = None,
    ) -> EvalResult: ...

    @property
    @abstractmethod
    def name(self) -> str: ...
