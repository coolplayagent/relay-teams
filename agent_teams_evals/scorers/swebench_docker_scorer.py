from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.scorers.base import Scorer

if TYPE_CHECKING:
    from agent_teams_evals.workspace.base import PreparedWorkspace


def _run_pytest(container_id: str, tests: list[str], timeout: float = 180.0) -> bool:
    if not tests:
        return True
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
            "--tb=no",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode == 0


class SWEBenchDockerScorer(Scorer):
    """Score by running fail_to_pass and pass_to_pass tests inside the container."""

    def __init__(self, pytest_timeout: float = 180.0) -> None:
        self._pytest_timeout = pytest_timeout

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
                agent_output=agent_output,
                token_usage=token_usage,
                duration_seconds=duration_seconds,
                error=error,
            )

        container_id = workspace.container_id
        f2p_tests = list(item.fail_to_pass)
        p2p_tests = list(item.pass_to_pass)

        try:
            f2p_ok = _run_pytest(container_id, f2p_tests, self._pytest_timeout)
            p2p_ok = _run_pytest(container_id, p2p_tests, self._pytest_timeout)
        except subprocess.TimeoutExpired:
            f2p_ok = False
            p2p_ok = False

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
            workspace_path=str(workspace.repo_path) if workspace else None,
            error=error,
        )
