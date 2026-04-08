from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import typer

from relay_teams_evals.backends.base import AgentBackend
from relay_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from relay_teams_evals.scorers.base import Scorer
from relay_teams_evals.workspace.base import (
    PreparedWorkspace,
    WorkspaceSetup,
    WorkspaceSetupError,
)
from relay_teams_evals.workspace.patch_extractor import PatchExtractor


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


def _build_intent(item: EvalItem) -> str:
    return item.intent


def _null_workspace(item: EvalItem) -> PreparedWorkspace:
    return PreparedWorkspace(item_id=item.item_id, repo_path=Path("."), base_commit="")


class _AttemptFailedError(Exception):
    def __init__(
        self,
        *,
        stage: str,
        prepared: PreparedWorkspace | None,
        cause: Exception,
        metadata_emitted: bool = False,
        token_usage_emitted: bool = False,
    ) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.prepared = prepared
        self.cause = cause
        self.metadata_emitted = metadata_emitted
        self.token_usage_emitted = token_usage_emitted


class _ArtifactCollectorLike(Protocol):
    def collect(
        self,
        item: EvalItem,
        result: EvalResult,
        workspace: PreparedWorkspace | None,
    ) -> None: ...


class EvalRunner:
    def __init__(
        self,
        backend: AgentBackend,
        scorer: Scorer,
        workspace_setup: WorkspaceSetup | None = None,
        patch_extractor: PatchExtractor | None = None,
        artifact_collector: _ArtifactCollectorLike | None = None,
        keep_workspaces: bool = False,
        concurrency: int = 1,
        infra_retry_attempts: int = 2,
        infra_retry_backoff_seconds: float = 5.0,
    ) -> None:
        self._backend = backend
        self._scorer = scorer
        self._workspace_setup = workspace_setup
        self._patch_extractor = patch_extractor
        self._artifact_collector = artifact_collector
        self._keep_workspaces = keep_workspaces
        self._concurrency = concurrency
        self._infra_retry_attempts = max(0, infra_retry_attempts)
        self._infra_retry_backoff_seconds = max(0.0, infra_retry_backoff_seconds)

    def _build_exception_result(
        self,
        *,
        item: EvalItem,
        started_at: datetime,
        duration_seconds: float,
        scorer_detail: str,
        error: str,
        build_log_path: str | None,
        build_error_summary: str | None,
    ) -> EvalResult:
        return EvalResult(
            item_id=item.item_id,
            dataset=item.dataset,
            run_id="",
            session_id="",
            outcome=RunOutcome.FAILED,
            passed=False,
            score=0.0,
            scorer_name=self._scorer.name,
            scorer_detail=scorer_detail,
            generated_patch="",
            raw_generated_patch="",
            filtered_generated_files=(),
            duration_seconds=duration_seconds,
            started_at=started_at,
            error=error,
            build_log_path=build_log_path,
            build_error_summary=build_error_summary,
        )

    def _cleanup_workspace(self, prepared: PreparedWorkspace | None) -> None:
        if (
            not self._keep_workspaces
            and prepared is not None
            and self._workspace_setup is not None
        ):
            try:
                self._workspace_setup.cleanup(prepared)
            except Exception:
                pass

    def _is_retryable_infra_failure(self, error: _AttemptFailedError) -> bool:
        cause = error.cause
        if error.stage == "prepare":
            if isinstance(cause, WorkspaceSetupError):
                return cause.retryable
            return not isinstance(cause, ValueError)
        if error.stage == "backend" and not error.metadata_emitted:
            return isinstance(cause, (OSError, RuntimeError, TimeoutError))
        return False

    def _run_item_once(
        self,
        item: EvalItem,
    ) -> tuple[EvalResult, PreparedWorkspace | None]:
        prepared: PreparedWorkspace | None = None
        metadata_emitted = False
        token_usage_emitted = False

        try:
            if self._workspace_setup is not None:
                _log(item.item_id, f"cloning {item.repo_url} @ {item.base_commit} ...")
                try:
                    prepared = self._workspace_setup.prepare(item)
                except Exception as exc:
                    raise _AttemptFailedError(
                        stage="prepare",
                        prepared=prepared,
                        cause=exc,
                    ) from exc
                _log(item.item_id, f"repo ready: {prepared.repo_path}")

            intent = _build_intent(item)
            workspace = prepared if prepared is not None else _null_workspace(item)

            run_id = ""
            session_id = ""
            text_parts: list[str] = []
            input_tokens = 0
            cached_input_tokens = 0
            output_tokens = 0
            reasoning_output_tokens = 0
            total_requests = 0
            total_tool_calls = 0
            outcome = RunOutcome.TIMEOUT

            try:
                for event in self._backend.run(
                    intent, workspace, keep_workspace=self._keep_workspaces
                ):
                    match event.type:
                        case "metadata":
                            metadata_emitted = True
                            run_id = event.run_id
                            session_id = event.session_id
                        case "text_delta":
                            text_parts.append(event.text)
                        case "token_usage":
                            token_usage_emitted = True
                            input_tokens += event.input_tokens
                            cached_input_tokens += event.cached_input_tokens
                            output_tokens += event.output_tokens
                            reasoning_output_tokens += event.reasoning_output_tokens
                            total_requests += event.requests
                            total_tool_calls += event.tool_calls
                        case "completed":
                            outcome = RunOutcome.COMPLETED
                            break
                        case "failed":
                            outcome = RunOutcome.FAILED
                            break
                        case "stopped":
                            outcome = RunOutcome.STOPPED
                            break
            except Exception as exc:
                raise _AttemptFailedError(
                    stage="backend",
                    prepared=prepared,
                    cause=exc,
                    metadata_emitted=metadata_emitted,
                    token_usage_emitted=token_usage_emitted,
                ) from exc

            agent_output = "".join(text_parts)
            _log(
                item.item_id,
                f"run finished: outcome={outcome.value} output_chars={len(agent_output)}",
            )

            token_usage = TokenUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_output_tokens=reasoning_output_tokens,
                total_tokens=input_tokens + output_tokens,
                total_requests=total_requests,
                total_tool_calls=total_tool_calls,
            )

            generated_patch = ""
            raw_generated_patch = ""
            if self._patch_extractor is not None and prepared is not None:
                try:
                    raw_generated_patch = self._patch_extractor.extract(prepared)
                except Exception as exc:
                    raise _AttemptFailedError(
                        stage="patch_extract",
                        prepared=prepared,
                        cause=exc,
                        metadata_emitted=metadata_emitted,
                        token_usage_emitted=token_usage_emitted,
                    ) from exc
                generated_patch = raw_generated_patch
                _log(item.item_id, f"generated patch: {len(generated_patch)} chars")

            try:
                result = self._scorer.score(
                    item=item,
                    run_id=run_id,
                    session_id=session_id,
                    outcome=outcome,
                    agent_output=agent_output,
                    generated_patch=generated_patch,
                    raw_generated_patch=raw_generated_patch,
                    filtered_generated_files=(),
                    token_usage=token_usage,
                    duration_seconds=0.0,
                    workspace=prepared,
                    error=None,
                )
            except Exception as exc:
                raise _AttemptFailedError(
                    stage="score",
                    prepared=prepared,
                    cause=exc,
                    metadata_emitted=metadata_emitted,
                    token_usage_emitted=token_usage_emitted,
                ) from exc

            return result, prepared

        except _AttemptFailedError:
            raise
        except Exception as exc:
            raise _AttemptFailedError(
                stage="score",
                prepared=prepared,
                cause=exc,
                metadata_emitted=metadata_emitted,
                token_usage_emitted=token_usage_emitted,
            ) from exc

    def run_item(self, item: EvalItem) -> EvalResult:
        started_at = datetime.now(tz=timezone.utc)
        t_start = time.monotonic()
        prepared: PreparedWorkspace | None = None
        result: EvalResult | None = None
        total_attempts = self._infra_retry_attempts + 1

        try:
            for attempt_number in range(1, total_attempts + 1):
                try:
                    result, prepared = self._run_item_once(item)
                    break
                except _AttemptFailedError as exc:
                    is_retryable = (
                        attempt_number < total_attempts
                        and self._is_retryable_infra_failure(exc)
                    )
                    if is_retryable:
                        self._cleanup_workspace(exc.prepared)
                        _log(
                            item.item_id,
                            "retryable infra failure on attempt "
                            f"{attempt_number}/{total_attempts}: {exc.cause}",
                        )
                        if self._infra_retry_backoff_seconds > 0:
                            _log(
                                item.item_id,
                                "retrying after "
                                f"{self._infra_retry_backoff_seconds:.1f}s backoff ...",
                            )
                            time.sleep(self._infra_retry_backoff_seconds)
                        continue

                    _log(item.item_id, f"ERROR: {exc.cause}")
                    build_log_path = None
                    build_error_summary = None
                    scorer_detail = "exception during run"
                    if isinstance(exc.cause, WorkspaceSetupError):
                        build_log_path = exc.cause.build_log_path
                        build_error_summary = exc.cause.build_error_summary
                        if build_log_path is not None:
                            scorer_detail = "instance image build failed"
                    result = self._build_exception_result(
                        item=item,
                        started_at=started_at,
                        duration_seconds=0.0,
                        scorer_detail=scorer_detail,
                        error=str(exc.cause),
                        build_log_path=build_log_path,
                        build_error_summary=build_error_summary,
                    )
                    prepared = exc.prepared
                    break

            if result is None:
                raise RuntimeError("eval run produced no result")

            result = result.model_copy(
                update={
                    "duration_seconds": time.monotonic() - t_start,
                    "started_at": started_at,
                }
            )

        finally:
            if self._artifact_collector is not None and result is not None:
                try:
                    self._artifact_collector.collect(item, result, prepared)
                except Exception:
                    _log(item.item_id, "failed to collect artifacts")

            self._cleanup_workspace(prepared)

        if result is None:
            raise RuntimeError("eval run produced no result")
        return result

    def run_all(self, items: list[EvalItem]) -> list[EvalResult]:
        if self._concurrency <= 1:
            return [self.run_item(item) for item in items]
        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            futures = {pool.submit(self.run_item, item): item for item in items}
            return [f.result() for f in as_completed(futures)]
