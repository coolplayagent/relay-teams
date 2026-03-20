from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path
from typing import cast

from typer.testing import CliRunner

from agent_teams_evals.checkpoint import EvalCheckpointStore, build_checkpoint_signature
from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.run import _validate_unique_item_ids, app
from agent_teams_evals.run_config import RunConfig

runner = CliRunner()


def _item(item_id: str) -> EvalItem:
    return EvalItem(item_id=item_id, dataset="jsonl", intent=f"intent-{item_id}")


def _result(item_id: str, *, score: float, passed: bool = True) -> EvalResult:
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
        duration_seconds=0.1,
    )


class _FakeBackend:
    def __init__(self, _config) -> None:
        pass


class _FakeScorer:
    @property
    def name(self) -> str:
        return "keyword"


class _FakeLoader:
    def __init__(self, items: list[EvalItem], dataset_name: str | None = None) -> None:
        self._items = items
        self._dataset_name = dataset_name

    def load(self, _path: Path) -> list[EvalItem]:
        return list(self._items)


def _install_fake_backend_module(monkeypatch) -> None:
    backend_module = types.ModuleType("agent_teams_evals.backends.agent_teams")
    setattr(backend_module, "AgentTeamsBackend", _FakeBackend)
    monkeypatch.setitem(
        cast(dict[str, types.ModuleType], sys.modules),
        "agent_teams_evals.backends.agent_teams",
        backend_module,
    )


def test_validate_unique_item_ids_rejects_duplicates() -> None:
    items = [_item("a"), _item("a")]

    try:
        _validate_unique_item_ids(items)
    except ValueError as exc:
        assert "Duplicate item_id values are not supported: a" == str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected duplicate item ids to fail")


def test_run_resumes_completed_items_and_keeps_report_order(
    monkeypatch, tmp_path: Path
) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        '{"item_id":"placeholder","intent":"demo"}\n', encoding="utf-8"
    )
    output_dir = tmp_path / "results"
    items = [_item("a"), _item("b"), _item("c")]
    cfg = RunConfig(
        dataset_path=dataset_path,
        output_dir=output_dir,
        concurrency=2,
        save_artifacts=False,
        report_format="json",
    )
    checkpoint_store = EvalCheckpointStore(output_dir)
    checkpoint_store.ensure_initialized(
        build_checkpoint_signature(
            cfg,
            dataset_path=dataset_path,
            item_ids=tuple(item.item_id for item in items),
        )
    )
    checkpoint_store.append_result(_result("b", score=0.2, passed=False))

    run_calls: list[str] = []

    class FakeEvalRunner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_item(self, item: EvalItem) -> EvalResult:
            run_calls.append(item.item_id)
            if item.item_id == "a":
                time.sleep(0.05)
                return _result("a", score=1.0, passed=True)
            if item.item_id == "c":
                time.sleep(0.01)
                return _result("c", score=0.7, passed=True)
            raise AssertionError(f"unexpected item: {item.item_id}")

    monkeypatch.setattr(
        "agent_teams_evals.run_config.load_run_config",
        lambda _path: cfg,
    )
    _install_fake_backend_module(monkeypatch)
    monkeypatch.setattr(
        "agent_teams_evals.loaders.jsonl_loader.JsonlLoader",
        lambda dataset_name: _FakeLoader(items, dataset_name),
    )
    monkeypatch.setattr(
        "agent_teams_evals.scorers.keyword_scorer.KeywordScorer",
        _FakeScorer,
    )
    monkeypatch.setattr("agent_teams_evals.runner.EvalRunner", FakeEvalRunner)

    config_file = tmp_path / "eval.yaml"
    config_file.write_text("unused: true\n", encoding="utf-8")
    result = runner.invoke(app, ["run", "--config", str(config_file)])

    assert result.exit_code == 0
    assert set(run_calls) == {"a", "c"}
    assert "b" not in run_calls
    assert "Resuming from checkpoint: 1 completed, 2 remaining" in result.output

    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert [entry["item_id"] for entry in report["results"]] == ["a", "b", "c"]

    loaded = checkpoint_store.load_results()
    assert sorted(loaded) == ["a", "b", "c"]


def test_run_restart_archives_previous_output_dir_before_new_run(
    monkeypatch, tmp_path: Path
) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        '{"item_id":"placeholder","intent":"demo"}\n', encoding="utf-8"
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    (output_dir / "stale.txt").write_text("old-run", encoding="utf-8")
    items = [_item("a")]
    cfg = RunConfig(
        dataset_path=dataset_path,
        output_dir=output_dir,
        save_artifacts=False,
        report_format="json",
    )
    run_calls: list[str] = []

    class FakeEvalRunner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_item(self, item: EvalItem) -> EvalResult:
            run_calls.append(item.item_id)
            return _result(item.item_id, score=1.0, passed=True)

    monkeypatch.setattr(
        "agent_teams_evals.run_config.load_run_config",
        lambda _path: cfg,
    )
    _install_fake_backend_module(monkeypatch)
    monkeypatch.setattr(
        "agent_teams_evals.loaders.jsonl_loader.JsonlLoader",
        lambda dataset_name: _FakeLoader(items, dataset_name),
    )
    monkeypatch.setattr(
        "agent_teams_evals.scorers.keyword_scorer.KeywordScorer",
        _FakeScorer,
    )
    monkeypatch.setattr("agent_teams_evals.runner.EvalRunner", FakeEvalRunner)

    config_file = tmp_path / "eval.yaml"
    config_file.write_text("unused: true\n", encoding="utf-8")
    result = runner.invoke(app, ["run", "--config", str(config_file), "--restart"])

    assert result.exit_code == 0
    assert run_calls == ["a"]
    archived_dirs = [
        path for path in tmp_path.iterdir() if path.name.startswith("results.")
    ]
    assert len(archived_dirs) == 1
    assert (archived_dirs[0] / "stale.txt").read_text(encoding="utf-8") == "old-run"
    assert (output_dir / "report.json").exists()
    assert (output_dir / "checkpoint.meta.json").exists()


def test_run_fails_when_checkpoint_signature_does_not_match(
    monkeypatch, tmp_path: Path
) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        '{"item_id":"placeholder","intent":"demo"}\n', encoding="utf-8"
    )
    output_dir = tmp_path / "results"
    cfg = RunConfig(
        dataset_path=dataset_path,
        output_dir=output_dir,
        save_artifacts=False,
        report_format="json",
    )
    existing_items = (_item("a"),)
    checkpoint_store = EvalCheckpointStore(output_dir)
    checkpoint_store.ensure_initialized(
        build_checkpoint_signature(
            cfg,
            dataset_path=dataset_path,
            item_ids=tuple(item.item_id for item in existing_items),
        )
    )

    run_calls: list[str] = []

    class FakeEvalRunner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_item(self, item: EvalItem) -> EvalResult:
            run_calls.append(item.item_id)
            return _result(item.item_id, score=1.0, passed=True)

    monkeypatch.setattr(
        "agent_teams_evals.run_config.load_run_config",
        lambda _path: cfg,
    )
    _install_fake_backend_module(monkeypatch)
    monkeypatch.setattr(
        "agent_teams_evals.loaders.jsonl_loader.JsonlLoader",
        lambda dataset_name: _FakeLoader([_item("a"), _item("b")], dataset_name),
    )
    monkeypatch.setattr(
        "agent_teams_evals.scorers.keyword_scorer.KeywordScorer",
        _FakeScorer,
    )
    monkeypatch.setattr("agent_teams_evals.runner.EvalRunner", FakeEvalRunner)

    config_file = tmp_path / "eval.yaml"
    config_file.write_text("unused: true\n", encoding="utf-8")
    result = runner.invoke(app, ["run", "--config", str(config_file)])

    assert result.exit_code == 1
    assert (
        "Checkpoint signature does not match the current eval configuration."
        in result.output
    )
    assert run_calls == []
