from __future__ import annotations

from pathlib import Path

from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.scorers import swebench_docker_scorer
from agent_teams_evals.scorers.swebench_docker_scorer import (
    PytestOutcome,
    SWEBenchDockerScorer,
)
from agent_teams_evals.workspace.base import PreparedWorkspace

_PATCH = "diff --git a/pkg.py b/pkg.py\n@@ -1 +1 @@\n-old_value\n+new_value\n"


def _make_item(
    *,
    test_patch: str | None = None,
) -> EvalItem:
    return EvalItem(
        item_id="demo",
        dataset="swebench",
        intent="demo",
        reference_patch=_PATCH,
        test_patch=test_patch,
        fail_to_pass=("tests.test_fix",),
        pass_to_pass=("tests.test_keep",),
    )


def _make_workspace() -> PreparedWorkspace:
    return PreparedWorkspace(
        item_id="demo",
        repo_path=Path("."),
        base_commit="abc123",
        container_id="container-1",
        container_repo_path="/testbed",
    )


def _score(item: EvalItem, workspace: PreparedWorkspace) -> EvalResult:
    return SWEBenchDockerScorer().score(
        item=item,
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        agent_output="",
        generated_patch=_PATCH,
        token_usage=TokenUsage(),
        duration_seconds=1.0,
        workspace=workspace,
        error=None,
    )


def test_records_patch_jaccard_auxiliary_score(monkeypatch) -> None:
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_run_pytest",
        lambda *_a, **_kw: PytestOutcome(passed=True, output="1 passed"),
    )

    result = _score(_make_item(), _make_workspace())

    assert result.passed is True
    assert result.score == 1.0
    assert result.auxiliary_scores["patch_jaccard"].score == 1.0
    assert "aux.patch_jaccard=1.000" in result.scorer_detail


def test_no_test_patch_skips_apply(monkeypatch) -> None:
    """When item.test_patch is None, _apply_test_patch must not be called."""
    called = []
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_apply_test_patch",
        lambda *_a, **_kw: (called.append(1), (True, ""))[1],
    )
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_run_pytest",
        lambda *_a, **_kw: PytestOutcome(passed=True, output=""),
    )

    result = _score(_make_item(test_patch=None), _make_workspace())

    assert result.passed is True
    assert called == []


def test_test_patch_applied_before_pytest(monkeypatch) -> None:
    apply_calls: list[str] = []

    def fake_apply(
        container_id: str, patch: str, repo: str, **_kw: object
    ) -> tuple[bool, str]:
        apply_calls.append(patch)
        return True, ""

    monkeypatch.setattr(swebench_docker_scorer, "_apply_test_patch", fake_apply)
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_run_pytest",
        lambda *_a, **_kw: PytestOutcome(passed=True, output=""),
    )

    result = _score(
        _make_item(test_patch="diff --git a/t.py b/t.py\n"),
        _make_workspace(),
    )

    assert result.passed is True
    assert len(apply_calls) == 1
    assert apply_calls[0] == "diff --git a/t.py b/t.py\n"


def test_test_patch_failure_skips_pytest(monkeypatch) -> None:
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_apply_test_patch",
        lambda *_a, **_kw: (False, "patch does not apply"),
    )
    pytest_calls: list[object] = []
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_run_pytest",
        lambda *_a, **_kw: (
            pytest_calls.append(1),
            PytestOutcome(passed=True, output=""),
        )[1],
    )

    result = _score(
        _make_item(test_patch="bad patch"),
        _make_workspace(),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert "FAILED" in result.scorer_detail
    assert "test_patch apply failed" in result.scorer_log
    assert "patch does not apply" in result.scorer_log
    assert pytest_calls == []


def test_scorer_log_captures_pytest_output(monkeypatch) -> None:
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_run_pytest",
        lambda cid, tests, *_a, **_kw: (
            PytestOutcome(
                passed=False,
                output="FAILED tests.test_fix - AssertionError",
            )
            if any("test_fix" in t for t in tests)
            else PytestOutcome(passed=True, output="1 passed")
        ),
    )

    result = _score(_make_item(), _make_workspace())

    assert result.passed is False
    assert "fail_to_pass" in result.scorer_log
    assert "FAILED tests.test_fix" in result.scorer_log
    assert "pass_to_pass" in result.scorer_log
    assert "1 passed" in result.scorer_log
