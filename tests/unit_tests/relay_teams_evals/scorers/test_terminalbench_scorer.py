from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from relay_teams_evals.models import EvalItem, RunOutcome, TokenUsage
from relay_teams_evals.scorers.terminalbench_scorer import TerminalBenchScorer
from relay_teams_evals.workspace.base import PreparedWorkspace


def _write_task(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "task.yaml").write_text(
        "\n".join(
            [
                "instruction: 'Create the expected output'",
                "difficulty: easy",
                "category: software_engineering",
                "parser_name: pytest",
                "max_test_timeout_sec: 5",
            ]
        ),
        encoding="utf-8",
    )
    (path / "run-tests.sh").write_text("#!/bin/bash\npytest -rA\n", encoding="utf-8")
    tests_dir = path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_outputs.py").write_text(
        "def test_expected():\n    assert True\n",
        encoding="utf-8",
    )


def test_terminalbench_scorer_passes_when_parser_results_pass(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    task_path = tmp_path / "task"
    _write_task(task_path)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["docker", "exec"] and "bash" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "================ short test summary info ================\n"
                    "PASSED test_outputs.py::test_expected\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    item = EvalItem(
        item_id="task",
        dataset="terminalbench",
        intent="Create the expected output",
        extra_fields={"terminalbench_task_path": str(task_path)},
    )
    workspace = PreparedWorkspace(
        item_id="task",
        repo_path=task_path,
        base_commit="terminalbench",
        container_id="container-1",
        container_repo_path="/work",
        terminalbench_task_path=task_path,
    )

    result = TerminalBenchScorer().score(
        item=item,
        run_id="run",
        session_id="session",
        outcome=RunOutcome.COMPLETED,
        agent_output="done",
        generated_patch="",
        raw_generated_patch="",
        filtered_generated_files=(),
        token_usage=TokenUsage(),
        duration_seconds=1.0,
        workspace=workspace,
    )

    assert result.passed is True
    assert result.score == 1.0
    assert "tests=1/1" in result.scorer_detail
    assert any(cmd[:2] == ["docker", "cp"] for cmd in calls)


def test_terminalbench_scorer_fails_on_parse_error(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    task_path = tmp_path / "task"
    _write_task(task_path)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "exec"] and "bash" in cmd:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="no pytest summary", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    item = EvalItem(
        item_id="task",
        dataset="terminalbench",
        intent="Create the expected output",
        extra_fields={"terminalbench_task_path": str(task_path)},
    )
    workspace = PreparedWorkspace(
        item_id="task",
        repo_path=task_path,
        base_commit="terminalbench",
        container_id="container-1",
        container_repo_path="/work",
        terminalbench_task_path=task_path,
    )

    result = TerminalBenchScorer().score(
        item=item,
        run_id="run",
        session_id="session",
        outcome=RunOutcome.COMPLETED,
        agent_output="done",
        generated_patch="",
        raw_generated_patch="",
        filtered_generated_files=(),
        token_usage=TokenUsage(),
        duration_seconds=1.0,
        workspace=workspace,
    )

    assert result.passed is False
    assert result.score == 0.0
    assert "scoring error" in result.scorer_detail
