from __future__ import annotations

from pathlib import Path
import subprocess

from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.scorers import swebench_docker_scorer
from agent_teams_evals.scorers.swebench_docker_scorer import (
    PytestOutcome,
    SWEBenchDockerScorer,
    _sanitize_test_ids,
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
        raw_generated_patch=_PATCH,
        filtered_generated_files=(),
        token_usage=TokenUsage(),
        duration_seconds=1.0,
        workspace=workspace,
        error=None,
    )


def test_records_patch_jaccard_auxiliary_score(monkeypatch) -> None:
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_apply_test_patch",
        lambda *_a, **_kw: (True, ""),
    )
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


def test_apply_test_patch_omits_allow_empty_flag(monkeypatch) -> None:
    calls: list[tuple[list[str], object, bool]] = []

    def fake_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((cmd, kwargs.get("input"), bool(kwargs.get("text", False))))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(swebench_docker_scorer.subprocess, "run", fake_run)

    ok, err = swebench_docker_scorer._apply_test_patch(
        "container-1",
        "diff --git a/t.py b/t.py\n",
    )

    assert ok is True
    assert err == ""
    assert calls == [
        (
            [
                "docker",
                "exec",
                "-i",
                "container-1",
                "git",
                "-C",
                "/testbed",
                "apply",
                "-",
            ],
            b"diff --git a/t.py b/t.py\n",
            False,
        )
    ]


def test_run_pytest_uses_testbed_python_and_repo_path(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "1 passed", "")

    monkeypatch.setattr(swebench_docker_scorer.subprocess, "run", fake_run)

    result = swebench_docker_scorer._run_pytest(
        "container-1",
        ["tests.test_fix"],
        "/repo",
    )

    assert result.passed is True
    assert result.output == "1 passed"
    assert calls == [
        [
            "docker",
            "exec",
            "-i",
            "-w",
            "/repo",
            "container-1",
            "/opt/miniconda3/envs/testbed/bin/python",
            "-c",
            swebench_docker_scorer._PYTEST_STDIN_RUNNER,
        ]
    ]


def test_no_test_patch_skips_apply(monkeypatch) -> None:
    """When item.test_patch is None, only the candidate patch apply should run."""
    apply_calls: list[str] = []
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_apply_test_patch",
        lambda _cid, patch, *_a, **_kw: (apply_calls.append(patch), (True, ""))[1],
    )
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_run_pytest",
        lambda *_a, **_kw: PytestOutcome(passed=True, output=""),
    )

    result = _score(_make_item(test_patch=None), _make_workspace())

    assert result.passed is True
    assert apply_calls == [_PATCH]


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
    assert apply_calls == [_PATCH, "diff --git a/t.py b/t.py\n"]


def test_candidate_patch_failure_skips_test_patch_and_pytest(monkeypatch) -> None:
    apply_calls: list[str] = []

    def fake_apply(
        _container_id: str, patch: str, _repo: str, **_kw: object
    ) -> tuple[bool, str]:
        apply_calls.append(patch)
        if patch == _PATCH:
            return False, "candidate patch does not apply"
        return True, ""

    monkeypatch.setattr(swebench_docker_scorer, "_apply_test_patch", fake_apply)
    pytest_calls: list[int] = []
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_run_pytest",
        lambda *_a, **_kw: (
            pytest_calls.append(1),
            PytestOutcome(passed=True, output=""),
        )[1],
    )

    result = _score(
        _make_item(test_patch="diff --git a/t.py b/t.py\n"), _make_workspace()
    )

    assert result.passed is False
    assert result.score == 0.0
    assert "candidate_patch apply failed" in result.scorer_detail
    assert "candidate patch apply failed" in result.scorer_log
    assert apply_calls == [_PATCH]
    assert pytest_calls == []


def test_test_patch_failure_skips_pytest(monkeypatch) -> None:
    apply_calls: list[str] = []

    def fake_apply(
        _container_id: str, patch: str, _repo: str, **_kw: object
    ) -> tuple[bool, str]:
        apply_calls.append(patch)
        if patch == _PATCH:
            return True, ""
        return False, "patch does not apply"

    monkeypatch.setattr(
        swebench_docker_scorer,
        "_apply_test_patch",
        fake_apply,
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
    assert apply_calls == [_PATCH, "bad patch"]
    assert pytest_calls == []


def test_scorer_log_captures_pytest_output(monkeypatch) -> None:
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_apply_test_patch",
        lambda *_a, **_kw: (True, ""),
    )
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


class TestSanitizeTestIds:
    def test_valid_ids_unchanged(self) -> None:
        ids = [
            "tests/test_table.py::TestMeta::test_mapping_init[meta0]",
            "tests/test_table.py::TestMeta::test_plain",
        ]
        assert _sanitize_test_ids(ids) == ids

    def test_truncated_bracket_falls_back_to_base_name(self) -> None:
        ids = [
            "tests/test_table.py::TestMeta::test_non_mapping_init[ceci",
            "tests/test_table.py::TestMeta::test_non_mapping_set[ceci",
        ]
        assert _sanitize_test_ids(ids) == [
            "tests/test_table.py::TestMeta::test_non_mapping_init",
            "tests/test_table.py::TestMeta::test_non_mapping_set",
        ]

    def test_mixed_valid_and_truncated(self) -> None:
        ids = [
            "tests/test_a.py::test_ok[param]",
            "tests/test_b.py::test_broken[ceci",
            "tests/test_c.py::test_plain",
        ]
        assert _sanitize_test_ids(ids) == [
            "tests/test_a.py::test_ok[param]",
            "tests/test_b.py::test_broken",
            "tests/test_c.py::test_plain",
        ]

    def test_empty_list(self) -> None:
        assert _sanitize_test_ids([]) == []


def test_filtered_test_files_are_reported(monkeypatch) -> None:
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_apply_test_patch",
        lambda *_a, **_kw: (True, ""),
    )
    monkeypatch.setattr(
        swebench_docker_scorer,
        "_run_pytest",
        lambda *_a, **_kw: PytestOutcome(passed=True, output="1 passed"),
    )

    result = SWEBenchDockerScorer().score(
        item=_make_item(),
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        agent_output="",
        generated_patch=_PATCH,
        raw_generated_patch=_PATCH
        + "diff --git a/tests/test_fix.py b/tests/test_fix.py\n",
        filtered_generated_files=("tests/test_fix.py",),
        token_usage=TokenUsage(),
        duration_seconds=1.0,
        workspace=_make_workspace(),
        error=None,
    )

    assert result.passed is True
    assert "filtered_test_files=1" in result.scorer_detail
    assert "filtered benchmark test file changes" in result.scorer_log
    assert "tests/test_fix.py" in result.scorer_log
