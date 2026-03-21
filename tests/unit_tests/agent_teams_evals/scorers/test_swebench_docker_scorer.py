from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.scorers import swebench_docker_scorer
from agent_teams_evals.scorers.swebench_docker_scorer import SWEBenchDockerScorer
from agent_teams_evals.workspace.base import PreparedWorkspace

_PATCH = "diff --git a/pkg.py b/pkg.py\n@@ -1 +1 @@\n-old_value\n+new_value\n"

_SWEBENCH_INSTANCE = {
    "instance_id": "demo",
    "repo": "astropy/astropy",
    "version": "4.3",
    "base_commit": "abc123",
    "patch": _PATCH,
    "test_patch": "",
    "problem_statement": "fix it",
    "hints_text": "",
    "created_at": "2024-01-01",
    "FAIL_TO_PASS": '["tests/test_fix.py::test_fix"]',
    "PASS_TO_PASS": '["tests/test_keep.py::test_keep"]',
    "environment_setup_commit": "abc123",
}


def _make_item(
    *,
    swebench_instance: dict[str, str] | None = None,
) -> EvalItem:
    return EvalItem(
        item_id="demo",
        dataset="swebench",
        intent="demo",
        reference_patch=_PATCH,
        test_patch="",
        fail_to_pass=("tests/test_fix.py::test_fix",),
        pass_to_pass=("tests/test_keep.py::test_keep",),
        swebench_instance=swebench_instance or _SWEBENCH_INSTANCE,
    )


def _make_workspace() -> PreparedWorkspace:
    return PreparedWorkspace(
        item_id="demo",
        repo_path=Path("."),
        base_commit="abc123",
        container_id="container-1",
        container_repo_path="/testbed",
    )


def _make_scorer() -> SWEBenchDockerScorer:
    return SWEBenchDockerScorer(client=MagicMock())


def _score(
    item: EvalItem,
    workspace: PreparedWorkspace,
    scorer: SWEBenchDockerScorer | None = None,
) -> EvalResult:
    if scorer is None:
        scorer = _make_scorer()
    return scorer.score(
        item=item,
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        agent_output="",
        generated_patch=_PATCH,
        raw_generated_patch=_PATCH,
        filtered_generated_files=(),
        token_usage=TokenUsage(),
        duration_seconds=1.0,
        workspace=workspace,
        error=None,
    )


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_resolved_gives_score_one(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
) -> None:
    mock_make_spec.return_value = MagicMock()
    mock_run.return_value = {"completed": True, "resolved": True}

    result = _score(_make_item(), _make_workspace())

    assert result.passed is True
    assert result.score == 1.0
    assert "resolved" in result.scorer_detail


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_not_resolved_gives_score_zero(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
) -> None:
    mock_make_spec.return_value = MagicMock()
    mock_run.return_value = {"completed": True, "resolved": False}

    result = _score(_make_item(), _make_workspace())

    assert result.passed is False
    assert result.score == 0.0
    assert "not resolved" in result.scorer_detail


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_evaluation_not_completed(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
) -> None:
    mock_make_spec.return_value = MagicMock()
    mock_run.return_value = {"completed": False, "resolved": False}

    result = _score(_make_item(), _make_workspace())

    assert result.passed is False
    assert result.score == 0.0
    assert "evaluation did not complete" in result.scorer_log


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(
    swebench_docker_scorer, "_read_test_output", return_value="PASSED all tests"
)
def test_scorer_log_includes_test_output(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
) -> None:
    mock_make_spec.return_value = MagicMock()
    mock_run.return_value = {"completed": True, "resolved": True}

    result = _score(_make_item(), _make_workspace())

    assert "PASSED all tests" in result.scorer_log
    assert "test output" in result.scorer_log


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_records_patch_jaccard_auxiliary_score(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
) -> None:
    mock_make_spec.return_value = MagicMock()
    mock_run.return_value = {"completed": True, "resolved": True}

    result = _score(_make_item(), _make_workspace())

    assert result.passed is True
    assert result.score == 1.0
    assert result.auxiliary_scores["patch_jaccard"].score == 1.0
    assert "aux.patch_jaccard=1.000" in result.scorer_detail


@patch.object(
    swebench_docker_scorer,
    "_run_instance_crlf_safe",
    side_effect=RuntimeError("docker crash"),
)
@patch.object(swebench_docker_scorer, "make_test_spec")
def test_scoring_error_returns_zero(
    mock_make_spec: MagicMock,
    _mock_run: MagicMock,
) -> None:
    mock_make_spec.return_value = MagicMock()

    result = _score(_make_item(), _make_workspace())

    assert result.passed is False
    assert result.score == 0.0
    assert "scoring error" in result.scorer_detail
    assert "docker crash" in result.scorer_log


def test_missing_swebench_instance_raises() -> None:
    item = EvalItem(
        item_id="demo",
        dataset="swebench",
        intent="demo",
        swebench_instance=None,
    )
    result = _score(item, _make_workspace())
    assert result.passed is False
    assert result.score == 0.0
    assert "scoring error" in result.scorer_detail


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_cached_report_is_cleaned(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure stale report.json is removed before calling run_instance."""
    mock_make_spec.return_value = MagicMock()
    mock_run.return_value = {"completed": True, "resolved": True}

    # Point RUN_EVALUATION_LOG_DIR to tmp_path so we can create a fake cached report
    monkeypatch.setattr(  # type: ignore[union-attr]
        swebench_docker_scorer, "RUN_EVALUATION_LOG_DIR", str(tmp_path)
    )
    report_dir = tmp_path / "score-run-1" / "agent-teams" / "demo"
    report_dir.mkdir(parents=True)
    cached = report_dir / "report.json"
    cached.write_text("{}")

    _score(_make_item(), _make_workspace())

    assert not cached.exists()
