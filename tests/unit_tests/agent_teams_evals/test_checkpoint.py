from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_teams_evals.checkpoint import (
    EvalCheckpointSignature,
    EvalCheckpointStore,
    archive_output_dir,
)
from agent_teams_evals.models import EvalResult, RunOutcome, TokenUsage


def _signature() -> EvalCheckpointSignature:
    return EvalCheckpointSignature(
        dataset="jsonl",
        dataset_path="D:/tmp/dataset.jsonl",
        dataset_sha256="abc123",
        item_ids=("a", "b"),
        scorer="keyword",
        swebench_pass_threshold=0.8,
        backend="agent_teams",
        workspace_mode="git",
        agent_execution_mode="ai",
        agent_yolo=True,
        agent_timeout_seconds=600.0,
        git_clone_timeout_seconds=120.0,
    )


def _result(item_id: str, *, score: float, passed: bool) -> EvalResult:
    return EvalResult(
        item_id=item_id,
        dataset="jsonl",
        run_id=f"run-{item_id}",
        session_id=f"session-{item_id}",
        outcome=RunOutcome.COMPLETED,
        passed=passed,
        score=score,
        scorer_name="keyword",
        token_usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        duration_seconds=1.0,
    )


def test_checkpoint_store_load_results_last_write_wins_and_ignores_corrupt_tail(
    tmp_path: Path,
) -> None:
    store = EvalCheckpointStore(tmp_path / "results")
    store.ensure_initialized(_signature())
    store.append_result(_result("a", score=0.1, passed=False))
    store.append_result(_result("a", score=1.0, passed=True))
    store.append_result(_result("b", score=0.5, passed=True))

    with store.results_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"broken": ')

    loaded = store.load_results()

    assert sorted(loaded) == ["a", "b"]
    assert loaded["a"].score == 1.0
    assert loaded["a"].passed is True
    assert loaded["b"].score == 0.5


def test_archive_output_dir_moves_existing_contents_to_timestamped_sibling(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    old_report = output_dir / "report.json"
    old_report.write_text(json.dumps({"old": True}), encoding="utf-8")

    archived = archive_output_dir(
        output_dir,
        now=datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc),
    )

    assert archived == tmp_path / "results.20260320T000000Z"
    assert output_dir.exists() is False
    assert archived is not None
    assert (archived / "report.json").read_text(encoding="utf-8") == '{"old": true}'
