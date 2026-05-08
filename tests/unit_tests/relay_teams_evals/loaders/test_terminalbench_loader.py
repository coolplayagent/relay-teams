from __future__ import annotations

from pathlib import Path

from relay_teams_evals.loaders.terminalbench_loader import TerminalBenchLoader
from relay_teams_evals.workspace.terminalbench_setup import TerminalBenchConfig


def _write_task(path: Path, instruction: str = "Create /tmp/answer.txt") -> None:
    path.mkdir(parents=True)
    (path / "task.yaml").write_text(
        "\n".join(
            [
                f"instruction: {instruction!r}",
                "difficulty: easy",
                "category: software_engineering",
                "parser_name: pytest",
            ]
        ),
        encoding="utf-8",
    )
    (path / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (path / "run-tests.sh").write_text("#!/bin/bash\n", encoding="utf-8")


def test_loads_terminalbench_task_directory(tmp_path: Path) -> None:
    task_dir = tmp_path / "hello-task"
    _write_task(task_dir)

    items = TerminalBenchLoader().load(task_dir)

    assert len(items) == 1
    item = items[0]
    assert item.item_id == "hello-task"
    assert item.dataset == "terminalbench"
    assert "Create /tmp/answer.txt" in item.intent
    assert item.extra_fields["terminalbench_task_path"] == str(task_dir.resolve())
    assert item.extra_fields["terminalbench_parser"] == "pytest"


def test_loads_terminalbench_dataset_directory_in_sorted_order(tmp_path: Path) -> None:
    _write_task(tmp_path / "b-task", "Do b")
    _write_task(tmp_path / "a-task", "Do a")

    items = TerminalBenchLoader().load(tmp_path)

    assert [item.item_id for item in items] == ["a-task", "b-task"]


def test_auto_downloads_dataset_when_path_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dataset_path = tmp_path / "downloaded"

    def fake_download(self: TerminalBenchLoader, path: Path) -> Path:
        _ = self
        _write_task(path / "downloaded-task", "Downloaded task")
        return path

    monkeypatch.setattr(TerminalBenchLoader, "_download_dataset", fake_download)

    loader = TerminalBenchLoader(
        TerminalBenchConfig(
            auto_download_dataset=True,
            dataset_name="terminal-bench-core",
            dataset_version="head",
        )
    )

    items = loader.load(dataset_path)

    assert [item.item_id for item in items] == ["downloaded-task"]
