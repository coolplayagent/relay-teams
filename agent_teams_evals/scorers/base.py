from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import JsonValue

from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage


class Scorer(ABC):
    @abstractmethod
    def score(
        self,
        *,
        item: EvalItem,
        run_id: str,
        session_id: str,
        outcome: RunOutcome,
        events: list[dict[str, JsonValue]],
        agent_output: str,
        generated_patch: str,
        token_usage: TokenUsage,
        duration_seconds: float,
        workspace_path: str | None = None,
        error: str | None = None,
    ) -> EvalResult: ...

    @property
    @abstractmethod
    def name(self) -> str: ...
