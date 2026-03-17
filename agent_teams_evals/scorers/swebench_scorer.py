from __future__ import annotations

import re

from pydantic import JsonValue

from agent_teams_evals.config import EvalConfig
from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.scorers.base import Scorer

_DIFF_HEADER = re.compile(r"^diff --git", re.MULTILINE)


def _extract_changed_lines(patch: str) -> set[str]:
    lines: set[str] = set()
    for line in patch.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            lines.add(line[1:].strip())
    return lines


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _try_extract_patch_from_output(agent_output: str) -> str:
    match = _DIFF_HEADER.search(agent_output)
    if match:
        return agent_output[match.start() :]
    return ""


class SWEBenchScorer(Scorer):
    def __init__(self, config: EvalConfig) -> None:
        self._threshold = config.swebench_pass_threshold

    @property
    def name(self) -> str:
        return "swebench"

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
        patch = generated_patch
        if not patch:
            patch = _try_extract_patch_from_output(agent_output)

        if item.reference_patch and patch:
            ref_lines = _extract_changed_lines(item.reference_patch)
            gen_lines = _extract_changed_lines(patch)
            score_val = _jaccard(ref_lines, gen_lines)
            passed = score_val >= self._threshold
            detail = (
                f"jaccard={score_val:.3f} (threshold={self._threshold}); "
                f"ref_lines={len(ref_lines)}, gen_lines={len(gen_lines)}"
            )
        elif item.reference_patch and not patch:
            score_val = 0.0
            passed = False
            detail = "no patch generated; reference patch exists"
        else:
            passed = outcome == RunOutcome.COMPLETED
            score_val = 1.0 if passed else 0.0
            detail = f"no reference patch; pass based on completion: {outcome.value}"

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
            generated_patch=patch,
            token_usage=token_usage,
            duration_seconds=duration_seconds,
            workspace_path=workspace_path,
            error=error,
        )
