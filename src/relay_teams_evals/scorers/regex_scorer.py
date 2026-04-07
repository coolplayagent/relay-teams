from __future__ import annotations

import re
from typing import TYPE_CHECKING

from relay_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from relay_teams_evals.scorers.base import Scorer

if TYPE_CHECKING:
    from relay_teams_evals.workspace.base import PreparedWorkspace


class RegexScorer(Scorer):
    @property
    def name(self) -> str:
        return "regex"

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
        if not item.expected_patterns:
            passed = outcome == RunOutcome.COMPLETED
            detail = "no patterns defined; pass based on completion"
            score_val = 1.0 if passed else 0.0
        else:
            unmatched = [
                p for p in item.expected_patterns if not re.search(p, agent_output)
            ]
            passed = len(unmatched) == 0
            matched = len(item.expected_patterns) - len(unmatched)
            total = len(item.expected_patterns)
            score_val = matched / total if total > 0 else 0.0
            detail = (
                f"unmatched patterns: {unmatched}"
                if unmatched
                else f"all {total} patterns matched"
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
