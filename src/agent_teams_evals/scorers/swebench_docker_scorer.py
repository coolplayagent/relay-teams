from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_teams_evals.models import (
    AuxiliaryScore,
    EvalItem,
    EvalResult,
    RunOutcome,
    TokenUsage,
)
from agent_teams_evals.scorers.base import Scorer
from agent_teams_evals.scorers.swebench_scorer import build_patch_jaccard_score

if TYPE_CHECKING:
    from agent_teams_evals.workspace.base import PreparedWorkspace


@dataclass(frozen=True)
class PytestOutcome:
    passed: bool
    output: str


def _apply_test_patch(
    container_id: str,
    test_patch: str,
    repo_path: str = "/testbed",
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """Apply the SWE-bench test_patch inside the container via ``git apply``."""
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container_id,
            "git",
            "-C",
            repo_path,
            "apply",
            "--allow-empty",
            "-",
        ],
        input=test_patch,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode == 0:
        return True, ""
    return False, result.stderr.strip()


def _run_pytest(
    container_id: str,
    tests: list[str],
    timeout: float = 180.0,
) -> PytestOutcome:
    if not tests:
        return PytestOutcome(passed=True, output="")
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_id,
            "python",
            "-m",
            "pytest",
            *tests,
            "-x",
            "--tb=short",
            "-q",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = result.stdout or ""
    if result.stderr:
        output += "\n--- stderr ---\n" + result.stderr
    return PytestOutcome(passed=result.returncode == 0, output=output)


class SWEBenchDockerScorer(Scorer):
    """Score by running fail_to_pass and pass_to_pass tests inside the container."""

    def __init__(
        self,
        pytest_timeout: float = 180.0,
        patch_pass_threshold: float = 0.8,
    ) -> None:
        self._pytest_timeout = pytest_timeout
        self._patch_pass_threshold = patch_pass_threshold

    @property
    def name(self) -> str:
        return "swebench_docker"

    def score(
        self,
        *,
        item: EvalItem,
        run_id: str,
        session_id: str,
        outcome: RunOutcome,
        agent_output: str,
        generated_patch: str,
        token_usage: TokenUsage,
        duration_seconds: float,
        workspace: PreparedWorkspace | None = None,
        error: str | None = None,
    ) -> EvalResult:
        auxiliary_scores: dict[str, AuxiliaryScore] = {}
        patch_score = build_patch_jaccard_score(
            reference_patch=item.reference_patch,
            generated_patch=generated_patch,
            agent_output=agent_output,
            pass_threshold=self._patch_pass_threshold,
        )
        if patch_score is not None:
            aux_name, aux_score = patch_score
            auxiliary_scores[aux_name] = aux_score

        if workspace is None or workspace.container_id is None:
            return EvalResult(
                item_id=item.item_id,
                dataset=item.dataset,
                run_id=run_id,
                session_id=session_id,
                outcome=outcome,
                passed=False,
                score=0.0,
                scorer_name=self.name,
                scorer_detail="no container available for docker scorer",
                auxiliary_scores=auxiliary_scores,
                agent_output=agent_output,
                token_usage=token_usage,
                duration_seconds=duration_seconds,
                error=error,
            )

        container_id = workspace.container_id
        f2p_tests = list(item.fail_to_pass)
        p2p_tests = list(item.pass_to_pass)
        scorer_log = ""

        try:
            # Apply the SWE-bench test_patch (adds/modifies test cases) before
            # running pytest so that fail_to_pass tests actually exist.
            test_patch_ok = True
            test_patch_err = ""
            if item.test_patch:
                repo = workspace.container_repo_path or "/testbed"
                test_patch_ok, test_patch_err = _apply_test_patch(
                    container_id,
                    item.test_patch,
                    repo,
                )

            if not test_patch_ok:
                f2p_result = PytestOutcome(
                    passed=False,
                    output=f"test_patch apply failed: {test_patch_err}",
                )
                p2p_result = PytestOutcome(passed=True, output="")
            else:
                f2p_result = _run_pytest(
                    container_id,
                    f2p_tests,
                    self._pytest_timeout,
                )
                p2p_result = _run_pytest(
                    container_id,
                    p2p_tests,
                    self._pytest_timeout,
                )

            f2p_ok = f2p_result.passed
            p2p_ok = p2p_result.passed

            log_parts: list[str] = []
            if test_patch_err:
                log_parts.append(f"=== test_patch apply error ===\n{test_patch_err}")
            if f2p_tests:
                log_parts.append(
                    f"=== fail_to_pass ({len(f2p_tests)} tests) ===\n"
                    f"{f2p_result.output}"
                )
            if p2p_tests:
                log_parts.append(
                    f"=== pass_to_pass ({len(p2p_tests)} tests) ===\n"
                    f"{p2p_result.output}"
                )
            scorer_log = "\n\n".join(log_parts)

        except subprocess.TimeoutExpired:
            f2p_ok = False
            p2p_ok = False
            scorer_log = f"pytest timed out after {self._pytest_timeout}s"

        passed = f2p_ok and p2p_ok
        if passed:
            score_val = 1.0
            detail = f"f2p={len(f2p_tests)} passed, p2p={len(p2p_tests)} passed"
        elif f2p_ok:
            score_val = 0.5
            detail = f"f2p={len(f2p_tests)} passed, p2p={len(p2p_tests)} FAILED (regressions)"
        else:
            score_val = 0.0
            detail = f"f2p={len(f2p_tests)} FAILED"

        patch_aux = auxiliary_scores.get("patch_jaccard")
        if patch_aux is not None:
            detail = f"{detail}; aux.patch_jaccard={patch_aux.score:.3f}"

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
            scorer_log=scorer_log,
            auxiliary_scores=auxiliary_scores,
            agent_output=agent_output,
            generated_patch=generated_patch,
            token_usage=token_usage,
            duration_seconds=duration_seconds,
            workspace_path=str(workspace.repo_path) if workspace else None,
            error=error,
        )
