from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

from terminal_bench.handlers.trial_handler import Task
from terminal_bench.parsers.base_parser import UnitTestStatus
from terminal_bench.parsers.parser_factory import ParserFactory

from relay_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from relay_teams_evals.scorers.base import Scorer
from relay_teams_evals.workspace.base import PreparedWorkspace


def _task_path(item: EvalItem, workspace: PreparedWorkspace | None) -> Path:
    if workspace is not None and workspace.terminalbench_task_path is not None:
        return workspace.terminalbench_task_path
    raw_path = item.extra_fields.get("terminalbench_task_path")
    if raw_path:
        return Path(raw_path)
    raise ValueError(f"Item {item.item_id} has no Terminal-Bench task path")


def _add_tests_to_tar(tar: tarfile.TarFile, task_path: Path) -> None:
    run_tests_path = task_path / "run-tests.sh"
    if not run_tests_path.exists():
        raise FileNotFoundError(f"Terminal-Bench task has no run-tests.sh: {task_path}")
    tar.add(run_tests_path, arcname="run-tests.sh")

    test_dir = task_path / "tests"
    if test_dir.exists():
        for child in test_dir.rglob("*"):
            if child.is_file():
                tar.add(child, arcname=child.relative_to(test_dir).as_posix())


def _copy_test_env(container_id: str, task_path: Path) -> None:
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        _add_tests_to_tar(tar, task_path)
    tar_stream.seek(0)

    subprocess.run(
        ["docker", "exec", container_id, "mkdir", "-p", "/tests"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["docker", "cp", "-", f"{container_id}:/tests"],
        input=tar_stream.read(),
        capture_output=True,
        check=True,
    )


def _format_parser_results(results: dict[str, UnitTestStatus] | None) -> str:
    if results is None:
        return "parser_results=none"
    passed = sum(1 for status in results.values() if status == UnitTestStatus.PASSED)
    return f"tests={passed}/{len(results)}"


class TerminalBenchScorer(Scorer):
    def __init__(self, test_timeout: float | None = None) -> None:
        self._test_timeout = test_timeout

    @property
    def name(self) -> str:
        return "terminalbench"

    def score(
        self,
        *,
        item: EvalItem,
        run_id: str,
        session_id: str,
        outcome: RunOutcome,
        agent_output: str,
        generated_patch: str,
        raw_generated_patch: str,
        filtered_generated_files: tuple[str, ...],
        token_usage: TokenUsage,
        duration_seconds: float,
        workspace: PreparedWorkspace | None = None,
        error: str | None = None,
    ) -> EvalResult:
        scorer_log = ""
        parser_results: dict[str, UnitTestStatus] | None = None
        passed = False
        detail = ""

        try:
            if workspace is None or not workspace.container_id:
                raise ValueError("Terminal-Bench scoring requires a running container")

            task_path = _task_path(item, workspace)
            task = Task.from_yaml(task_path / "task.yaml")
            parser = ParserFactory.get_parser(task.parser_name)
            timeout = self._test_timeout or task.max_test_timeout_sec

            _copy_test_env(workspace.container_id, task_path)
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-e",
                    "TEST_DIR=/tests",
                    "-w",
                    workspace.container_repo_path or "/",
                    workspace.container_id,
                    "bash",
                    "/tests/run-tests.sh",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            output = (result.stdout or "") + (
                "\n" + result.stderr if result.stderr else ""
            )
            parser_results = parser.parse(output)
            passed = all(
                status == UnitTestStatus.PASSED for status in parser_results.values()
            )
            detail = f"resolved={str(passed).lower()}; {_format_parser_results(parser_results)}; exit_code={result.returncode}"
            scorer_log = (
                "=== terminal-bench test output ===\n"
                f"{output}\n\n"
                "=== parser results ===\n"
                + "\n".join(
                    f"{name}: {status.value}" for name, status in parser_results.items()
                )
            )
        except subprocess.TimeoutExpired as exc:
            detail = f"test timeout after {exc.timeout}s"
            scorer_log = f"=== terminal-bench test timeout ===\n{exc}"
        except Exception as exc:
            detail = f"scoring error: {exc}"
            scorer_log = f"=== terminal-bench scoring error ===\n{exc}"

        return EvalResult(
            item_id=item.item_id,
            dataset=item.dataset,
            run_id=run_id,
            session_id=session_id,
            outcome=outcome,
            passed=passed,
            score=1.0 if passed else 0.0,
            scorer_name=self.name,
            scorer_detail=detail,
            scorer_log=scorer_log,
            agent_output=agent_output,
            generated_patch=generated_patch,
            raw_generated_patch=raw_generated_patch,
            filtered_generated_files=filtered_generated_files,
            token_usage=token_usage,
            duration_seconds=duration_seconds,
            workspace_path=(
                workspace.container_repo_path
                if workspace and workspace.container_repo_path
                else str(workspace.repo_path)
                if workspace
                else None
            ),
            error=error,
        )
