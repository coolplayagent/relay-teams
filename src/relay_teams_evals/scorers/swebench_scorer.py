from __future__ import annotations

import re
from typing import TYPE_CHECKING

from relay_teams_evals.models import (
    AuxiliaryScore,
    EvalItem,
    EvalResult,
    RunOutcome,
    TokenUsage,
)
from relay_teams_evals.scorers.base import Scorer

if TYPE_CHECKING:
    from relay_teams_evals.workspace.base import PreparedWorkspace

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


def build_patch_jaccard_score(
    *,
    reference_patch: str | None,
    generated_patch: str,
    agent_output: str,
    pass_threshold: float,
) -> tuple[str, AuxiliaryScore] | None:
    if reference_patch is None:
        return None

    patch = generated_patch or _try_extract_patch_from_output(agent_output)
    if patch:
        ref_lines = _extract_changed_lines(reference_patch)
        gen_lines = _extract_changed_lines(patch)
        score_val = _jaccard(ref_lines, gen_lines)
        detail = (
            f"jaccard={score_val:.3f} (threshold={pass_threshold}); "
            f"ref_lines={len(ref_lines)}, gen_lines={len(gen_lines)}"
        )
        return (
            "patch_jaccard",
            AuxiliaryScore(
                score=score_val,
                passed=score_val >= pass_threshold,
                detail=detail,
            ),
        )

    return (
        "patch_jaccard",
        AuxiliaryScore(
            score=0.0,
            passed=False,
            detail="no patch generated; reference patch exists",
        ),
    )


class SWEBenchScorer(Scorer):
    def __init__(self, pass_threshold: float = 0.8) -> None:
        self._threshold = pass_threshold

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
        agent_output: str,
        generated_patch: str,
        raw_generated_patch: str,
        filtered_generated_files: tuple[str, ...],
        token_usage: TokenUsage,
        duration_seconds: float,
        workspace: PreparedWorkspace | None = None,
        error: str | None = None,
    ) -> EvalResult:
        patch = generated_patch or _try_extract_patch_from_output(agent_output)
        auxiliary_scores: dict[str, AuxiliaryScore] = {}
        patch_score = build_patch_jaccard_score(
            reference_patch=item.reference_patch,
            generated_patch=patch,
            agent_output=agent_output,
            pass_threshold=self._threshold,
        )

        if patch_score is not None:
            aux_name, aux_score = patch_score
            auxiliary_scores[aux_name] = aux_score
            score_val = aux_score.score
            passed = bool(aux_score.passed)
            detail = aux_score.detail
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
            auxiliary_scores=auxiliary_scores,
            agent_output=agent_output,
            generated_patch=patch,
            raw_generated_patch=raw_generated_patch,
            filtered_generated_files=filtered_generated_files,
            token_usage=token_usage,
            duration_seconds=duration_seconds,
            workspace_path=str(workspace.repo_path) if workspace else None,
            error=error,
        )
