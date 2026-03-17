from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import typer
from pydantic import JsonValue

from agent_teams.interfaces.sdk.client import AgentTeamsClient

from agent_teams_evals.config import EvalConfig
from agent_teams_evals.models import EvalItem, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.scorers.base import Scorer
from agent_teams_evals.workspace.base import WorkspaceSetup
from agent_teams_evals.workspace.patch_extractor import PatchExtractor

_TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "run_stopped"})


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


def _build_enriched_intent(item: EvalItem, *, with_repo_context: bool) -> str:
    if with_repo_context:
        return (
            f"Repository: {item.repo_url} (commit {item.base_commit})\n\n{item.intent}"
        )
    return item.intent


def _try_delete_workspace(client: AgentTeamsClient, workspace_id: str) -> None:
    try:
        client.delete_workspace(workspace_id)
    except Exception:
        pass


class EvalRunner:
    def __init__(
        self,
        config: EvalConfig,
        scorer: Scorer,
        workspace_setup: WorkspaceSetup | None = None,
        patch_extractor: PatchExtractor | None = None,
    ) -> None:
        self._config = config
        self._scorer = scorer
        self._workspace_setup = workspace_setup
        self._patch_extractor = patch_extractor
        self._client = AgentTeamsClient(
            base_url=config.base_url,
            stream_timeout_seconds=config.run_timeout_seconds,
        )

    def run_item(self, item: EvalItem) -> EvalResult:
        started_at = datetime.now(tz=timezone.utc)
        t_start = time.monotonic()
        prepared = None
        eval_workspace_id: str | None = None

        try:
            if self._workspace_setup is not None:
                _log(item.item_id, f"cloning {item.repo_url} @ {item.base_commit} ...")
                prepared = self._workspace_setup.prepare(item)
                _log(item.item_id, f"repo ready: {prepared.repo_path}")

            if prepared is not None:
                eval_workspace_id = f"eval-{item.item_id}"
                _log(item.item_id, f"registering workspace {eval_workspace_id!r} ...")
                _try_delete_workspace(self._client, eval_workspace_id)
                self._client.create_workspace(
                    workspace_id=eval_workspace_id,
                    root_path=str(prepared.repo_path.resolve()),
                )
                session_workspace_id = eval_workspace_id
            else:
                session_workspace_id = self._config.workspace_id

            intent = _build_enriched_intent(
                item, with_repo_context=prepared is not None
            )

            _log(
                item.item_id,
                f"creating session (workspace={session_workspace_id!r}) ...",
            )
            session_data = self._client.create_session(
                workspace_id=session_workspace_id
            )
            session_id = str(session_data.get("session_id", ""))
            _log(item.item_id, f"session: {session_id}")

            _log(item.item_id, "creating run ...")
            run_handle = self._client.create_run(
                intent=intent,
                session_id=session_id,
                execution_mode=self._config.execution_mode,
                approval_mode="yolo",
            )
            run_id = run_handle.run_id
            _log(item.item_id, f"run: {run_id}")

            events: list[dict[str, JsonValue]] = []
            text_parts: list[str] = []
            input_tokens = 0
            output_tokens = 0
            outcome = RunOutcome.TIMEOUT
            event_count = 0

            _log(item.item_id, "streaming events ...")
            for event in self._client.stream_run_events(run_id):
                events.append(event)
                event_type = event.get("event_type")
                event_count += 1

                if event_type == "token_usage":
                    data = event.get("data", {})
                    if isinstance(data, dict):
                        in_val = data.get("input_tokens", 0)
                        out_val = data.get("output_tokens", 0)
                        if isinstance(in_val, int):
                            input_tokens += in_val
                        if isinstance(out_val, int):
                            output_tokens += out_val
                    _log(
                        item.item_id,
                        f"[event #{event_count}] token_usage: "
                        f"in={input_tokens} out={output_tokens}",
                    )

                elif event_type == "text_delta":
                    data = event.get("data", {})
                    if isinstance(data, dict):
                        delta = data.get("text", "")
                        if isinstance(delta, str):
                            text_parts.append(delta)

                else:
                    _log(item.item_id, f"[event #{event_count}] {event_type}")

                if event_type in _TERMINAL_EVENTS:
                    if event_type == "run_completed":
                        outcome = RunOutcome.COMPLETED
                    elif event_type == "run_failed":
                        outcome = RunOutcome.FAILED
                    elif event_type == "run_stopped":
                        outcome = RunOutcome.STOPPED
                    break

            agent_output = "".join(text_parts)
            _log(
                item.item_id,
                f"run finished: outcome={outcome.value} "
                f"events={event_count} output_chars={len(agent_output)}",
            )

            token_usage = TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            )

            generated_patch = ""
            if self._patch_extractor is not None and prepared is not None:
                generated_patch = self._patch_extractor.extract(prepared)
                _log(item.item_id, f"generated patch: {len(generated_patch)} chars")

            duration = time.monotonic() - t_start

            result = self._scorer.score(
                item=item,
                run_id=run_id,
                session_id=session_id,
                outcome=outcome,
                events=events,
                agent_output=agent_output,
                generated_patch=generated_patch,
                token_usage=token_usage,
                duration_seconds=duration,
                workspace_path=str(prepared.repo_path)
                if prepared is not None
                else None,
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
                duration_seconds=duration,
                started_at=started_at,
                error=str(exc),
            )

        finally:
            if not self._config.keep_workspaces:
                if eval_workspace_id is not None:
                    _log(item.item_id, f"deleting workspace {eval_workspace_id!r} ...")
                    _try_delete_workspace(self._client, eval_workspace_id)
                if prepared is not None and self._workspace_setup is not None:
                    try:
                        self._workspace_setup.cleanup(prepared)
                    except Exception:
                        pass

        return result

    def run_all(self, items: list[EvalItem]) -> list[EvalResult]:
        if self._config.concurrency <= 1:
            return [self.run_item(item) for item in items]
        with ThreadPoolExecutor(max_workers=self._config.concurrency) as pool:
            futures = {pool.submit(self.run_item, item): item for item in items}
            return [f.result() for f in as_completed(futures)]
