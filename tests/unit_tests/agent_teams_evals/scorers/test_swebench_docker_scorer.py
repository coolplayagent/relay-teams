from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agent_teams_evals.models import (
    EvalItem,
    EvalResult,
    RunOutcome,
    SWEBenchResolutionStatus,
    TokenUsage,
)
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


def _local_tmp_dir(name: str) -> Path:
    path = Path(".tmp/agent_teams_evals_tests") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_report(
    tmp_path: Path,
    *,
    report_content: dict[str, object] | str,
    run_id: str = "run-1",
) -> Path:
    report_dir = tmp_path / f"score-{run_id}" / "agent-teams" / "demo"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.json"
    if isinstance(report_content, str):
        report_path.write_text(report_content, encoding="utf-8")
    else:
        report_path.write_text(json.dumps(report_content), encoding="utf-8")
    return report_path


def _mock_run_writes_report(
    tmp_path: Path,
    *,
    report_content: dict[str, object] | str,
    result: dict[str, object],
) -> Callable[..., dict[str, object]]:
    def _side_effect(**_kwargs) -> dict[str, object]:
        _write_report(tmp_path, report_content=report_content)
        return result

    return _side_effect


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
    assert result.swebench_diagnostics is not None
    assert (
        result.swebench_diagnostics.resolution_status == SWEBenchResolutionStatus.FULL
    )


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
    assert result.swebench_diagnostics is not None
    assert result.swebench_diagnostics.resolution_status == SWEBenchResolutionStatus.NO


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
    assert result.swebench_diagnostics is not None
    assert result.swebench_diagnostics.completed is False


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
    assert result.swebench_diagnostics is not None
    assert result.swebench_diagnostics.resolved is False


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_report_json_sets_full_resolution_diagnostics(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_make_spec.return_value = MagicMock()
    tmp_path = _local_tmp_dir("resolved-full")
    monkeypatch.setattr(swebench_docker_scorer, "RUN_EVALUATION_LOG_DIR", str(tmp_path))
    mock_run.side_effect = _mock_run_writes_report(
        tmp_path,
        report_content={
            "demo": {
                "patch_is_None": False,
                "patch_exists": True,
                "patch_successfully_applied": True,
                "resolved": True,
                "tests_status": {
                    "FAIL_TO_PASS": {
                        "success": ["tests/test_fix.py::test_fix"],
                        "failure": [],
                    },
                    "PASS_TO_PASS": {
                        "success": ["tests/test_keep.py::test_keep"],
                        "failure": [],
                    },
                    "FAIL_TO_FAIL": {"success": [], "failure": []},
                    "PASS_TO_FAIL": {"success": [], "failure": []},
                },
            }
        },
        result={"completed": True, "resolved": True},
    )

    result = _score(_make_item(), _make_workspace())

    assert result.passed is True
    assert result.swebench_diagnostics is not None
    assert (
        result.swebench_diagnostics.resolution_status == SWEBenchResolutionStatus.FULL
    )
    assert result.swebench_diagnostics.patch_successfully_applied is True
    assert result.swebench_diagnostics.tests_status.fail_to_pass.success == (
        "tests/test_fix.py::test_fix",
    )
    assert "resolution=full" in result.scorer_detail
    assert "f2p=1/1" in result.scorer_detail
    assert "p2p=1/1" in result.scorer_detail


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_report_json_partial_resolution_still_scores_zero(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_make_spec.return_value = MagicMock()
    tmp_path = _local_tmp_dir("resolved-partial")
    monkeypatch.setattr(swebench_docker_scorer, "RUN_EVALUATION_LOG_DIR", str(tmp_path))
    mock_run.side_effect = _mock_run_writes_report(
        tmp_path,
        report_content={
            "demo": {
                "patch_is_None": False,
                "patch_exists": True,
                "patch_successfully_applied": True,
                "resolved": False,
                "tests_status": {
                    "FAIL_TO_PASS": {
                        "success": ["tests/test_fix.py::test_fix"],
                        "failure": ["tests/test_fix.py::test_other"],
                    },
                    "PASS_TO_PASS": {
                        "success": ["tests/test_keep.py::test_keep"],
                        "failure": [],
                    },
                    "FAIL_TO_FAIL": {"success": [], "failure": []},
                    "PASS_TO_FAIL": {"success": [], "failure": []},
                },
            }
        },
        result={"completed": True, "resolved": False},
    )

    result = _score(_make_item(), _make_workspace())

    assert result.passed is False
    assert result.score == 0.0
    assert result.swebench_diagnostics is not None
    assert (
        result.swebench_diagnostics.resolution_status
        == SWEBenchResolutionStatus.PARTIAL
    )
    assert "resolution=partial" in result.scorer_detail
    assert "f2p=1/2" in result.scorer_detail
    assert "p2p=1/1" in result.scorer_detail


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_report_json_patch_apply_failure_is_structured(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_make_spec.return_value = MagicMock()
    tmp_path = _local_tmp_dir("patch-apply-failure")
    monkeypatch.setattr(swebench_docker_scorer, "RUN_EVALUATION_LOG_DIR", str(tmp_path))
    mock_run.side_effect = _mock_run_writes_report(
        tmp_path,
        report_content={
            "demo": {
                "patch_is_None": False,
                "patch_exists": True,
                "patch_successfully_applied": False,
                "resolved": False,
                "tests_status": {
                    "FAIL_TO_PASS": {"success": [], "failure": []},
                    "PASS_TO_PASS": {"success": [], "failure": []},
                    "FAIL_TO_FAIL": {"success": [], "failure": []},
                    "PASS_TO_FAIL": {"success": [], "failure": []},
                },
            }
        },
        result={"completed": False, "resolved": False},
    )

    result = _score(_make_item(), _make_workspace())

    assert result.passed is False
    assert result.swebench_diagnostics is not None
    assert result.swebench_diagnostics.patch_exists is True
    assert result.swebench_diagnostics.patch_successfully_applied is False
    assert "patch_applied=false" in result.scorer_detail
    assert "completed=false" in result.scorer_detail


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_missing_report_uses_top_level_bools(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_make_spec.return_value = MagicMock()
    mock_run.return_value = {"completed": True, "resolved": True}
    tmp_path = _local_tmp_dir("missing-report")
    monkeypatch.setattr(swebench_docker_scorer, "RUN_EVALUATION_LOG_DIR", str(tmp_path))

    result = _score(_make_item(), _make_workspace())

    assert result.passed is True
    assert result.swebench_diagnostics is not None
    assert result.swebench_diagnostics.resolved is True
    assert (
        result.swebench_diagnostics.resolution_status == SWEBenchResolutionStatus.FULL
    )
    assert "missing report.json" in result.scorer_log


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_invalid_report_falls_back_to_run_instance_result(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_make_spec.return_value = MagicMock()
    tmp_path = _local_tmp_dir("invalid-report")
    monkeypatch.setattr(swebench_docker_scorer, "RUN_EVALUATION_LOG_DIR", str(tmp_path))
    mock_run.side_effect = _mock_run_writes_report(
        tmp_path,
        report_content="{not json",
        result={"completed": True, "resolved": True},
    )

    result = _score(_make_item(), _make_workspace())

    assert result.passed is True
    assert result.swebench_diagnostics is not None
    assert result.swebench_diagnostics.resolved is True
    assert "report parse error" in result.scorer_log
    assert "resolution=full" in result.scorer_detail


@patch.object(swebench_docker_scorer, "_run_instance_crlf_safe")
@patch.object(swebench_docker_scorer, "make_test_spec")
@patch.object(swebench_docker_scorer, "_read_test_output", return_value="")
def test_cached_report_is_cleaned(
    _mock_read: MagicMock,
    mock_make_spec: MagicMock,
    mock_run: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure stale report.json is removed before calling run_instance."""
    mock_make_spec.return_value = MagicMock()
    mock_run.return_value = {"completed": True, "resolved": True}
    tmp_path = _local_tmp_dir("cached-report")

    # Point RUN_EVALUATION_LOG_DIR to tmp_path so we can create a fake cached report
    monkeypatch.setattr(swebench_docker_scorer, "RUN_EVALUATION_LOG_DIR", str(tmp_path))
    report_dir = tmp_path / "score-run-1" / "agent-teams" / "demo"
    report_dir.mkdir(parents=True)
    cached = report_dir / "report.json"
    cached.write_text("{}")

    deleted_paths: list[Path] = []

    def _fake_unlink(self: Path) -> None:
        deleted_paths.append(self)

    monkeypatch.setattr(Path, "unlink", _fake_unlink)
    _score(_make_item(), _make_workspace())

    assert deleted_paths == [cached]


def test_run_instance_wrapper_forces_utf8_open_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Handle:
        def __enter__(self) -> _Handle:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write(self, _data: str) -> int:
            return 0

    def _fake_open(
        file: str | bytes | int | Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
        closefd: bool = True,
        opener: Callable[[str, int], int] | None = None,
    ) -> _Handle:
        captured["file"] = file
        captured["mode"] = mode
        captured["buffering"] = buffering
        captured["encoding"] = encoding
        captured["errors"] = errors
        captured["newline"] = newline
        captured["closefd"] = closefd
        captured["opener"] = opener
        return _Handle()

    def _fake_upstream_run_instance(**_kwargs: object) -> dict[str, object]:
        with open("test_output.txt", "w") as handle:
            handle.write("contains angstrom Å")
        return {"completed": True, "resolved": True}

    monkeypatch.setattr(swebench_docker_scorer, "_IS_WINDOWS", True)
    monkeypatch.setattr(swebench_docker_scorer, "_ORIGINAL_OPEN", _fake_open)
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_upstream_run_instance",
        _fake_upstream_run_instance,
    )

    result = swebench_docker_scorer._run_instance_crlf_safe(
        test_spec=MagicMock(),
        pred={},
        rm_image=False,
        force_rebuild=False,
        client=MagicMock(),
        run_id="run-1",
        timeout=300,
    )

    assert result == {"completed": True, "resolved": True}
    assert captured["mode"] == "w"
    assert captured["encoding"] == "utf-8"
    assert captured["newline"] == "\n"
