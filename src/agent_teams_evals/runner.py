from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import typer

from agent_teams_evals.backends.base import AgentBackend
from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.scorers.base import Scorer
from agent_teams_evals.workspace.artifact_collector import ArtifactCollector
from agent_teams_evals.workspace.base import PreparedWorkspace, WorkspaceSetup
from agent_teams_evals.workspace.patch_extractor import PatchExtractor
from agent_teams_evals.workspace.patch_filter import filter_patch_for_swebench


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


def _build_intent(item: EvalItem) -> str:
    return item.intent


def _null_workspace(item: EvalItem) -> PreparedWorkspace:
    return PreparedWorkspace(item_id=item.item_id, repo_path=Path("."), base_commit="")


class EvalRunner:
    def __init__(
        self,
        backend: AgentBackend,
        scorer: Scorer,
        workspace_setup: WorkspaceSetup | None = None,
        patch_extractor: PatchExtractor | None = None,
        artifact_collector: ArtifactCollector | None = None,
        keep_workspaces: bool = False,
        concurrency: int = 1,
    ) -> None:
        self._backend = backend
        self._scorer = scorer
        self._workspace_setup = workspace_setup
        self._patch_extractor = patch_extractor
        self._artifact_collector = artifact_collector
        self._keep_workspaces = keep_workspaces
        self._concurrency = concurrency

    def run_item(self, item: EvalItem) -> EvalResult:
        started_at = datetime.now(tz=timezone.utc)
        t_start = time.monotonic()
        prepared: PreparedWorkspace | None = None
        scoring_workspace: PreparedWorkspace | None = None
        result: EvalResult | None = None

        try:
            if self._workspace_setup is not None:
                _log(item.item_id, f"cloning {item.repo_url} @ {item.base_commit} ...")
                prepared = self._workspace_setup.prepare(item)
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

            for event in self._backend.run(
                intent, workspace, keep_workspace=self._keep_workspaces
            ):
                match event.type:
                    case "metadata":
                        run_id = event.run_id
                        session_id = event.session_id
                    case "text_delta":
                        text_parts.append(event.text)
                    case "token_usage":
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
            filtered_generated_files: tuple[str, ...] = ()
            if self._patch_extractor is not None and prepared is not None:
                raw_generated_patch = self._patch_extractor.extract(prepared)
                generated_patch = raw_generated_patch
                if self._scorer.name == "swebench_docker":
                    filtered_patch = filter_patch_for_swebench(item, raw_generated_patch)
                    generated_patch = filtered_patch.scored_patch
                    filtered_generated_files = filtered_patch.filtered_files
                    if filtered_generated_files:
                        _log(
                            item.item_id,
                            f"filtered {len(filtered_generated_files)} benchmark test file change(s)",
                        )
                _log(item.item_id, f"generated patch: {len(generated_patch)} chars")

            if (
                self._scorer.name == "swebench_docker"
                and prepared is not None
                and self._workspace_setup is not None
            ):
                scoring_workspace = self._workspace_setup.prepare_score(item)
                _log(item.item_id, f"score repo ready: {scoring_workspace.repo_path}")

            duration = time.monotonic() - t_start

            result = self._scorer.score(
                item=item,
                run_id=run_id,
                session_id=session_id,
                outcome=outcome,
                agent_output=agent_output,
                generated_patch=generated_patch,
                raw_generated_patch=raw_generated_patch,
                filtered_generated_files=filtered_generated_files,
                token_usage=token_usage,
                duration_seconds=duration,
                workspace=scoring_workspace or prepared,
                error=None,
            )

        except Exception as exc:
            duration = time.monotonic() - t_start
            _log(item.item_id, f"ERROR: {exc}")
            result = EvalResult(
                item_id=item.item_id,
                dataset=item.dataset,
                run_id="",
                session_id="",
                outcome=RunOutcome.FAILED,
                passed=False,
                score=0.0,
                scorer_name=self._scorer.name,
                scorer_detail="exception during run",
                generated_patch="",
                raw_generated_patch="",
                filtered_generated_files=(),
                duration_seconds=duration,
                started_at=started_at,
                error=str(exc),
            )

        finally:
            if self._artifact_collector is not None and result is not None:
                try:
                    self._artifact_collector.collect(item, result, prepared)
                except Exception:
                    _log(item.item_id, "failed to collect artifacts")

            if (
                not self._keep_workspaces
                and scoring_workspace is not None
                and scoring_workspace != prepared
                and self._workspace_setup is not None
            ):
                try:
                    self._workspace_setup.cleanup(scoring_workspace)
                except Exception:
                    pass

            if (
                not self._keep_workspaces
                and prepared is not None
                and self._workspace_setup is not None
            ):
                try:
                    self._workspace_setup.cleanup(prepared)
                except Exception:
                    pass

        if result is None:
            raise RuntimeError("eval run produced no result")
        return result

    def run_all(self, items: list[EvalItem]) -> list[EvalResult]:
        if self._concurrency <= 1:
            return [self.run_item(item) for item in items]
        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            futures = {pool.submit(self.run_item, item): item for item in items}
            return [f.result() for f in as_completed(futures)]
