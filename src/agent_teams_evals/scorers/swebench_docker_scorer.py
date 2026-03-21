from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
import logging
import platform
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from unittest.mock import patch

from agent_teams_evals.models import (
    AuxiliaryScore,
    EvalItem,
    EvalResult,
    RunOutcome,
    TokenUsage,
)
from agent_teams_evals.scorers.base import Scorer
from agent_teams_evals.scorers.swebench_scorer import build_patch_jaccard_score

if TYPE_CHECKING:
    from agent_teams_evals.workspace.base import PreparedWorkspace


class _DockerClient(Protocol): ...


class _SWEBenchConstantsModule(Protocol):
    KEY_INSTANCE_ID: str
    KEY_MODEL: str
    KEY_PREDICTION: str
    LOG_TEST_OUTPUT: str
    RUN_EVALUATION_LOG_DIR: str | Path


class _SWEBenchRunEvaluationModule(Protocol):
    def run_instance(
        self,
        *,
        test_spec: object,
        pred: Mapping[str, str | None],
        rm_image: bool,
        force_rebuild: bool,
        client: _DockerClient,
        run_id: str,
        timeout: int | None = None,
    ) -> dict[str, object]: ...


class _SWEBenchTestSpecModule(Protocol):
    def make_test_spec(self, instance: Mapping[str, str]) -> object: ...


class _ResourceModule(types.ModuleType):
    RLIMIT_NOFILE: int

    def getrlimit(self, resource: int) -> tuple[int, int]:
        _ = resource
        return (0, 0)

    def setrlimit(self, resource: int, limits: tuple[int, int]) -> None:
        _ = (resource, limits)


def _load_swebench_dependencies() -> tuple[
    str,
    str,
    str,
    str,
    str | Path,
    Callable[..., dict[str, object]],
    Callable[[Mapping[str, str]], object],
]:
    # The swebench package imports ``resource`` (Unix-only) at package level via
    # ``prepare_images``. Stub it on Windows so the import chain succeeds; the
    # stub is never actually called during scoring.
    if platform.system() == "Windows" and "resource" not in sys.modules:
        resource_stub = _ResourceModule("resource")
        resource_stub.RLIMIT_NOFILE = 0
        sys.modules["resource"] = resource_stub

    constants_module = cast(
        _SWEBenchConstantsModule,
        import_module("swebench.harness.constants"),
    )
    run_evaluation_module = cast(
        _SWEBenchRunEvaluationModule,
        import_module("swebench.harness.run_evaluation"),
    )
    test_spec_module = cast(
        _SWEBenchTestSpecModule,
        import_module("swebench.harness.test_spec.test_spec"),
    )

    return (
        constants_module.KEY_INSTANCE_ID,
        constants_module.KEY_MODEL,
        constants_module.KEY_PREDICTION,
        constants_module.LOG_TEST_OUTPUT,
        constants_module.RUN_EVALUATION_LOG_DIR,
        run_evaluation_module.run_instance,
        test_spec_module.make_test_spec,
    )


(
    KEY_INSTANCE_ID,
    KEY_MODEL,
    KEY_PREDICTION,
    LOG_TEST_OUTPUT,
    RUN_EVALUATION_LOG_DIR,
    _upstream_run_instance,
    make_test_spec,
) = _load_swebench_dependencies()

_IS_WINDOWS = platform.system() == "Windows"


def _write_text_unix_newlines(
    self: Path,
    data: str,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> int:
    """``Path.write_text`` replacement that always writes Unix newlines.

    The swebench harness uses ``Path.write_text`` to create ``eval.sh`` and
    ``patch.diff`` locally before copying them into a Linux container.  On
    Windows, ``write_text`` defaults to CRLF which causes ``\\r`` to leak
    into the container scripts and break every shell command.
    """
    _ = newline
    return self.write_bytes(data.encode(encoding or "utf-8", errors or "strict"))


def _run_instance_crlf_safe(
    test_spec: object,
    pred: dict[str, str | None],
    rm_image: bool,
    force_rebuild: bool,
    client: _DockerClient,
    run_id: str,
    timeout: int | None = None,
) -> dict[str, object]:
    """Thin wrapper around the upstream ``run_instance`` that fixes
    Windows CRLF issues transparently."""
    if not _IS_WINDOWS:
        return _upstream_run_instance(
            test_spec=test_spec,
            pred=pred,
            rm_image=rm_image,
            force_rebuild=force_rebuild,
            client=client,
            run_id=run_id,
            timeout=timeout,
        )

    with patch.object(Path, "write_text", _write_text_unix_newlines):
        return _upstream_run_instance(
            test_spec=test_spec,
            pred=pred,
            rm_image=rm_image,
            force_rebuild=force_rebuild,
            client=client,
            run_id=run_id,
            timeout=timeout,
        )


_logger = logging.getLogger(__name__)

_MODEL_NAME = "agent-teams"


def _build_swebench_instance(item: EvalItem) -> dict[str, str]:
    """Reconstruct the raw SWE-bench instance dict expected by ``make_test_spec``."""
    if item.swebench_instance is not None:
        return dict(item.swebench_instance)
    raise ValueError(
        f"Item {item.item_id} has no swebench_instance data; "
        "cannot build TestSpec for official harness scoring"
    )


def _read_test_output(run_id: str, instance_id: str) -> str:
    """Read the test output log written by ``run_instance``."""
    log_path = (
        Path(RUN_EVALUATION_LOG_DIR)
        / run_id
        / _MODEL_NAME
        / instance_id
        / LOG_TEST_OUTPUT
    )
    if log_path.exists():
        return log_path.read_text(encoding="utf-8", errors="replace")
    return ""


class SWEBenchDockerScorer(Scorer):
    """Score by delegating test execution to the official swebench harness."""

    def __init__(
        self,
        client: _DockerClient,
        patch_pass_threshold: float = 0.8,
        test_timeout: int = 300,
    ) -> None:
        self._client = client
        self._patch_pass_threshold = patch_pass_threshold
        self._test_timeout = test_timeout

    @property
    def name(self) -> str:
        return "swebench_docker"

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
        auxiliary_scores: dict[str, AuxiliaryScore] = {}
        patch_score = build_patch_jaccard_score(
            reference_patch=item.reference_patch,
            generated_patch=generated_patch,
            agent_output=agent_output,
            pass_threshold=self._patch_pass_threshold,
        )
        if patch_score is not None:
            aux_name, aux_score = patch_score
            auxiliary_scores[aux_name] = aux_score

        scorer_log = ""
        resolved = False
        detail = ""

        try:
            instance_dict = _build_swebench_instance(item)
            test_spec = make_test_spec(instance_dict)

            pred = {
                KEY_INSTANCE_ID: item.item_id,
                KEY_MODEL: _MODEL_NAME,
                KEY_PREDICTION: generated_patch or None,
            }

            # Use a scorer-specific run_id to avoid collisions with agent run_ids
            scorer_run_id = f"score-{run_id}" if run_id else "score"

            # Remove any cached report from a previous run so swebench does not
            # short-circuit and return stale results.
            cached_report = (
                Path(RUN_EVALUATION_LOG_DIR)
                / scorer_run_id
                / _MODEL_NAME
                / item.item_id
                / "report.json"
            )
            if cached_report.exists():
                cached_report.unlink()

            result = _run_instance_crlf_safe(
                test_spec=test_spec,
                pred=pred,
                rm_image=False,
                force_rebuild=False,
                client=self._client,
                run_id=scorer_run_id,
                timeout=self._test_timeout,
            )

            resolved = bool(result.get("resolved", False))
            completed = bool(result.get("completed", False))

            test_output = _read_test_output(scorer_run_id, item.item_id)

            log_parts: list[str] = []
            if not completed:
                log_parts.append(
                    "=== swebench harness ===\nevaluation did not complete"
                )
            if test_output:
                log_parts.append(f"=== test output ===\n{test_output}")

            # Also include the swebench report.json if available
            report_path = (
                Path(RUN_EVALUATION_LOG_DIR)
                / scorer_run_id
                / _MODEL_NAME
                / item.item_id
                / "report.json"
            )
            if report_path.exists():
                report_content = report_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                log_parts.append(f"=== swebench report ===\n{report_content}")

            scorer_log = "\n\n".join(log_parts)

        except Exception as exc:
            _logger.exception("swebench scoring failed for %s", item.item_id)
            detail = f"scoring error: {exc}"
            scorer_log = f"=== scoring error ===\n{exc}"

        if not detail:
            score_val = 1.0 if resolved else 0.0
            detail = "resolved" if resolved else "not resolved"
            patch_aux = auxiliary_scores.get("patch_jaccard")
            if patch_aux is not None:
                detail = f"{detail}; aux.patch_jaccard={patch_aux.score:.3f}"
        else:
            score_val = 0.0

        return EvalResult(
            item_id=item.item_id,
            dataset=item.dataset,
            run_id=run_id,
            session_id=session_id,
            outcome=outcome,
            passed=resolved,
            score=score_val,
            scorer_name=self.name,
            scorer_detail=detail,
            scorer_log=scorer_log,
            auxiliary_scores=auxiliary_scores,
            agent_output=agent_output,
            generated_patch=generated_patch,
            raw_generated_patch=raw_generated_patch,
            filtered_generated_files=filtered_generated_files,
            token_usage=token_usage,
            duration_seconds=duration_seconds,
            workspace_path=str(workspace.repo_path) if workspace else None,
            error=error,
        )
