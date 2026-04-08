from __future__ import annotations

from typing import TYPE_CHECKING

from relay_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from relay_teams_evals.scorers.base import Scorer

if TYPE_CHECKING:
    from relay_teams_evals.workspace.base import PreparedWorkspace


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
        agent_output: str,
        generated_patch: str,
        raw_generated_patch: str,
        filtered_generated_files: tuple[str, ...],
        token_usage: TokenUsage,
        duration_seconds: float,
        workspace: PreparedWorkspace | None = None,
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
            detail = (
                f"missing keywords: {missing}"
                if missing
                else f"all {total} keywords found"
            )

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
            raw_generated_patch=raw_generated_patch,
            filtered_generated_files=filtered_generated_files,
            token_usage=token_usage,
            duration_seconds=duration_seconds,
            workspace_path=str(workspace.repo_path) if workspace else None,
            error=error,
        )
