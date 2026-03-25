from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import typer

from agent_teams_evals.models import EvalItem, EvalResult
from agent_teams_evals.workspace.base import PreparedWorkspace


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


class ArtifactCollector:
    """Persists replay data from a workspace before it is cleaned up.

    Artifacts are written to ``<output_dir>/artifacts/<item_id>/`` and include:

    - ``metadata.json``  -- item id, run/session ids, outcome, timing, etc.
    - ``patch.diff``     -- the scored patch after benchmark test-file filtering
    - ``raw_patch.diff`` -- the original extracted patch when different
    - ``agent_output.txt`` -- full agent text output
    - ``scorer_log.txt`` -- verbose scorer output (e.g. pytest stdout/stderr)
    - ``agent_teams.db`` -- the container's SQLite database (docker mode)
    - ``container.log``  -- container stdout/stderr (docker mode)
    """

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    def collect(
        self,
        item: EvalItem,
        result: EvalResult,
        workspace: PreparedWorkspace | None,
    ) -> None:
        artifact_dir = self._output_dir / "artifacts" / item.item_id
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir, ignore_errors=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        self._write_metadata(artifact_dir, item, result)
        self._write_patch(artifact_dir, result)
        self._write_agent_output(artifact_dir, result)
        self._write_scorer_log(artifact_dir, result)

        if workspace is not None and workspace.container_id is not None:
            self._collect_container_db(artifact_dir, workspace)
            self._collect_container_log(artifact_dir, workspace)

        _log(item.item_id, f"artifacts saved to {artifact_dir}")

    def _write_metadata(
        self, artifact_dir: Path, item: EvalItem, result: EvalResult
    ) -> None:
        metadata = {
            "item_id": item.item_id,
            "dataset": item.dataset,
            "repo_url": item.repo_url,
            "base_commit": item.base_commit,
            "run_id": result.run_id,
            "session_id": result.session_id,
            "outcome": result.outcome.value,
            "passed": result.passed,
            "score": result.score,
            "scorer_name": result.scorer_name,
            "scorer_detail": result.scorer_detail,
            "filtered_generated_files": list(result.filtered_generated_files),
            "auxiliary_scores": {
                name: score.model_dump()
                for name, score in sorted(result.auxiliary_scores.items())
            },
            "duration_seconds": result.duration_seconds,
            "token_usage": result.token_usage.model_dump(),
            "input_tokens": result.token_usage.input_tokens,
            "output_tokens": result.token_usage.output_tokens,
            "error": result.error,
            "build_log_path": result.build_log_path,
            "build_error_summary": result.build_error_summary,
            "collected_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        path = artifact_dir / "metadata.json"
        path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _write_patch(self, artifact_dir: Path, result: EvalResult) -> None:
        if result.generated_patch:
            path = artifact_dir / "patch.diff"
            path.write_text(result.generated_patch, encoding="utf-8")
        if (
            result.raw_generated_patch
            and result.raw_generated_patch != result.generated_patch
        ):
            raw_path = artifact_dir / "raw_patch.diff"
            raw_path.write_text(result.raw_generated_patch, encoding="utf-8")

    def _write_agent_output(self, artifact_dir: Path, result: EvalResult) -> None:
        if result.agent_output:
            path = artifact_dir / "agent_output.txt"
            path.write_text(result.agent_output, encoding="utf-8")

    def _write_scorer_log(self, artifact_dir: Path, result: EvalResult) -> None:
        if result.scorer_log:
            path = artifact_dir / "scorer_log.txt"
            path.write_text(result.scorer_log, encoding="utf-8")

    def _collect_container_db(
        self, artifact_dir: Path, workspace: PreparedWorkspace
    ) -> None:
        db_src = "/root/.agent-teams/agent_teams.db"
        db_dest = artifact_dir / "agent_teams.db"
        try:
            container_id = workspace.container_id or ""
            subprocess.run(
                ["docker", "cp", f"{container_id}:{db_src}", str(db_dest)],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, OSError):
            pass

    def _collect_container_log(
        self, artifact_dir: Path, workspace: PreparedWorkspace
    ) -> None:
        log_dest = artifact_dir / "container.log"
        try:
            container_id = workspace.container_id or ""
            result = subprocess.run(
                ["docker", "logs", container_id],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            output = result.stdout or ""
            if result.stderr:
                output += "\n--- stderr ---\n" + result.stderr
            if output.strip():
                log_dest.write_text(output, encoding="utf-8")
        except (subprocess.CalledProcessError, OSError):
            pass
