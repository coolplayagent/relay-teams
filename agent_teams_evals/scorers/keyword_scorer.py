from __future__ import annotations

from pydantic import JsonValue

from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.scorers.base import Scorer


class KeywordScorer(Scorer):
    @property
    def name(self) -> str:
        return "keyword"

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
    ) -> EvalResult:
        if not item.expected_keywords:
            passed = outcome == RunOutcome.COMPLETED
            detail = "no keywords defined; pass based on completion"
            score_val = 1.0 if passed else 0.0
        else:
            missing = [kw for kw in item.expected_keywords if kw not in agent_output]
            passed = len(missing) == 0
            matched = len(item.expected_keywords) - len(missing)
            total = len(item.expected_keywords)
            score_val = matched / total if total > 0 else 0.0
            if missing:
                detail = f"missing keywords: {missing}"
            else:
                detail = f"all {total} keywords found"

        return EvalResult(
            item_id=item.item_id,
            dataset=item.dataset,
            run_id=run_id,
            session_id=session_id,
            outcome=outcome,
            passed=passed,
            score=score_val,
            scorer_name=self.name,
            scorer_detail=detail,
            agent_output=agent_output,
            generated_patch=generated_patch,
            token_usage=token_usage,
            duration_seconds=duration_seconds,
            workspace_path=workspace_path,
            error=error,
        )
