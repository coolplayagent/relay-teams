from __future__ import annotations

from pathlib import Path

from terminal_bench.handlers.trial_handler import Task
from terminal_bench.registry.client import RegistryClient

from relay_teams_evals.loaders.base import DatasetLoader
from relay_teams_evals.models import EvalItem
from relay_teams_evals.workspace.terminalbench_setup import TerminalBenchConfig


def _task_dirs(path: Path) -> list[Path]:
    if (path / "task.yaml").exists():
        return [path]
    return sorted(
        child
        for child in path.iterdir()
        if child.is_dir() and (child / "task.yaml").exists()
    )


def build_terminalbench_intent(instruction: str) -> str:
    return (
        f"{instruction.strip()}\n\n"
        "You are solving a Terminal-Bench task inside the current container "
        "workspace. Make the requested changes using the available shell and "
        "file tools. Do not look for or modify hidden benchmark tests."
    )


class TerminalBenchLoader(DatasetLoader):
    def __init__(self, config: TerminalBenchConfig | None = None) -> None:
        self._config = config or TerminalBenchConfig()

    def _download_dataset(self, path: Path) -> Path:
        client = RegistryClient(
            registry_url=self._config.registry_url,
            local_registry_path=self._config.local_registry_path,
        )
        return client.download_dataset(
            self._config.dataset_name,
            self._config.dataset_version,
            overwrite=self._config.overwrite_dataset,
            output_dir=path,
        )

    def _ensure_dataset_path(self, path: Path) -> Path:
        should_overwrite = (
            self._config.auto_download_dataset and self._config.overwrite_dataset
        )
        if path.exists() and _task_dirs(path) and not should_overwrite:
            return path

        if not self._config.auto_download_dataset:
            if not path.exists():
                raise FileNotFoundError(
                    f"Terminal-Bench dataset path does not exist: {path}"
                )
            return path

        return self._download_dataset(path)

    def load(self, path: Path) -> list[EvalItem]:
        path = self._ensure_dataset_path(path)

        tasks = _task_dirs(path)
        if not tasks:
            raise ValueError(
                f"No Terminal-Bench task directories with task.yaml found in {path}"
            )

        items: list[EvalItem] = []
        for task_path in tasks:
            task = Task.from_yaml(task_path / "task.yaml")
            items.append(
                EvalItem(
                    item_id=task_path.name,
                    dataset="terminalbench",
                    intent=build_terminalbench_intent(task.instruction),
                    extra_fields={
                        "terminalbench_task_path": str(task_path.resolve()),
                        "terminalbench_difficulty": task.difficulty.value,
                        "terminalbench_category": task.category,
                        "terminalbench_parser": task.parser_name.value,
                    },
                )
            )
        return items
