from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import typer

app = typer.Typer(help="Agent benchmark evaluation CLI", add_completion=False)

if TYPE_CHECKING:
    from agent_teams_evals.checkpoint import EvalCheckpointSignature
    from agent_teams_evals.models import EvalItem, EvalReport, EvalResult
    from agent_teams_evals.reporter import EvalReporter
    from agent_teams_evals.run_config import RunConfig


class _DockerClient(Protocol): ...


class _DockerModule(Protocol):
    def from_env(self) -> _DockerClient: ...


def _load_docker_module() -> _DockerModule:
    return cast(_DockerModule, import_module("docker"))


def _validate_unique_item_ids(items: list[EvalItem]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        if item.item_id in seen and item.item_id not in duplicates:
            duplicates.append(item.item_id)
        seen.add(item.item_id)
    if duplicates:
        duplicate_text = ", ".join(sorted(duplicates))
        raise ValueError(
            f"Duplicate item_id values are not supported: {duplicate_text}"
        )


def _signature_without_item_ids(
    signature: EvalCheckpointSignature,
) -> dict[str, object]:
    payload = signature.model_dump()
    del payload["item_ids"]
    return payload


def _quote_cli_arg(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _normalize_item_ids(raw_item_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_item_ids:
        for part in raw_value.split(","):
            item_id = part.strip()
            if not item_id:
                raise ValueError(
                    "--item-ids contains an empty item id; "
                    "use comma-separated values like 'a,b,c'"
                )
            if item_id in seen:
                continue
            seen.add(item_id)
            normalized.append(item_id)
    return normalized


def _ordered_results(
    item_ids: tuple[str, ...],
    results_by_item_id: dict[str, EvalResult],
) -> list[EvalResult]:
    return [
        results_by_item_id[item_id]
        for item_id in item_ids
        if item_id in results_by_item_id
    ]


def _build_report_snapshot(
    *,
    cfg: RunConfig,
    scorer_name: str,
    item_ids: tuple[str, ...],
    results_by_item_id: dict[str, EvalResult],
) -> EvalReport:
    from agent_teams_evals.reporter import build_report

    return build_report(
        _ordered_results(item_ids, results_by_item_id),
        dataset=cfg.dataset,
        scorer_name=scorer_name,
        cost_per_million_input=cfg.cost_per_million_input_tokens,
        cost_per_million_cached_input=cfg.cost_per_million_cached_input_tokens,
        cost_per_million_output=cfg.cost_per_million_output_tokens,
        cost_per_million_reasoning_output=(
            cfg.cost_per_million_reasoning_output_tokens
        ),
    )


def _write_report_snapshot(
    *,
    cfg: RunConfig,
    reporter: EvalReporter,
    report: EvalReport,
) -> None:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = cfg.output_dir / "report.json"
    reporter.write_json(report, json_path)
    if cfg.report_format in ("html", "both"):
        html_path = cfg.output_dir / "report.html"
        reporter.write_html(report, html_path)


def _load_checkpoint_results(
    *,
    cfg: RunConfig,
    signature: EvalCheckpointSignature,
    rerun_item_ids: tuple[str, ...] = (),
) -> dict[str, EvalResult]:
    from agent_teams_evals.checkpoint import EvalCheckpointStore

    store = EvalCheckpointStore(cfg.output_dir)

    if not store.exists():
        if cfg.output_dir.exists() and any(cfg.output_dir.iterdir()):
            raise ValueError(
                f"Output directory already contains files but no checkpoint: {cfg.output_dir}"
            )
        store.ensure_initialized(signature)
        return {}

    existing_meta = store.load_meta()
    if existing_meta is None:
        raise ValueError(
            f"Checkpoint metadata is missing from output_dir: {cfg.output_dir}"
        )
    existing_signature = existing_meta.signature
    if rerun_item_ids:
        if _signature_without_item_ids(
            existing_signature
        ) != _signature_without_item_ids(signature):
            raise ValueError(
                "Checkpoint signature does not match the current eval configuration."
            )
        existing_item_ids = set(existing_signature.item_ids)
        missing_item_ids = [
            item_id for item_id in rerun_item_ids if item_id not in existing_item_ids
        ]
        if missing_item_ids:
            missing_text = ", ".join(sorted(missing_item_ids))
            raise ValueError(
                f"Cannot rerun items that are not in the checkpoint: {missing_text}"
            )
    else:
        store.ensure_initialized(signature)
    return store.load_results()


@app.command()
def run(
    config_file: Path = typer.Option(
        ..., "--config", "-c", help="Path to YAML run config"
    ),
    limit: int | None = typer.Option(None, help="Override: max items to evaluate"),
    item_ids: list[str] = typer.Option(
        [],
        help=(
            "Override: specific item IDs to run "
            "(repeat option or use comma-separated values)"
        ),
    ),
    concurrency: int | None = typer.Option(None, help="Override: parallel workers"),
    keep_workspaces: bool | None = typer.Option(None, help="Override: keep workspaces"),
    base_url: str | None = typer.Option(None, help="Override: backend base URL"),
    restart: bool = typer.Option(
        False, help="Archive the current output_dir and start a fresh eval run"
    ),
    rerun: bool = typer.Option(
        False,
        help="Force rerunning the selected --item-ids and overwrite their report/artifacts",
    ),
) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from agent_teams_evals.backends.agent_teams import AgentTeamsBackend
    from agent_teams_evals.checkpoint import (
        EvalCheckpointStore,
        archive_output_dir,
        build_checkpoint_signature,
    )
    from agent_teams_evals.loaders.jsonl_loader import JsonlLoader
    from agent_teams_evals.loaders.swebench_loader import SWEBenchLoader
    from agent_teams_evals.reporter import EvalReporter
    from agent_teams_evals.run_config import load_run_config
    from agent_teams_evals.runner import EvalRunner
    from agent_teams_evals.scorers.event_status_scorer import EventStatusScorer
    from agent_teams_evals.scorers.keyword_scorer import KeywordScorer
    from agent_teams_evals.scorers.regex_scorer import RegexScorer
    from agent_teams_evals.scorers.swebench_docker_scorer import SWEBenchDockerScorer
    from agent_teams_evals.scorers.swebench_scorer import SWEBenchScorer
    from agent_teams_evals.workspace.artifact_collector import ArtifactCollector
    from agent_teams_evals.workspace.docker_setup import DockerWorkspaceSetup
    from agent_teams_evals.workspace.git_setup import GitWorkspaceSetup
    from agent_teams_evals.workspace.patch_extractor import PatchExtractor

    cfg = load_run_config(config_file)
    try:
        normalized_item_ids = _normalize_item_ids(item_ids)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    # Apply CLI overrides
    overrides: dict[str, object] = {}
    if limit is not None:
        overrides["limit"] = limit
    if normalized_item_ids:
        overrides["item_ids"] = tuple(normalized_item_ids)
    if concurrency is not None:
        overrides["concurrency"] = concurrency
    if keep_workspaces is not None:
        overrides["keep_workspaces"] = keep_workspaces
    if overrides:
        cfg = cfg.model_copy(update=overrides)
    if base_url is not None:
        cfg = cfg.model_copy(
            update={
                "agent_teams": cfg.agent_teams.model_copy(update={"base_url": base_url})
            }
        )

    if cfg.dataset_path is None:
        typer.echo("Error: dataset_path is required in the config file.", err=True)
        raise typer.Exit(1)
    if rerun and not normalized_item_ids:
        typer.echo("Error: --rerun requires at least one --item-ids value.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Config: {config_file}")
    typer.echo(
        f"  dataset={cfg.dataset}  scorer={cfg.scorer}"
        f"  backend={cfg.backend}  workspace_mode={cfg.workspace_mode}"
        f"  concurrency={cfg.concurrency}"
    )

    # Backend
    match cfg.backend:
        case "agent_teams":
            backend = AgentTeamsBackend(cfg.agent_teams)
        case _:
            typer.echo(f"Error: unknown backend '{cfg.backend}'", err=True)
            raise typer.Exit(1)

    # Workspace setup
    workspace_setup = None
    patch_extractor = None
    match cfg.workspace_mode:
        case "docker":
            workspace_setup = DockerWorkspaceSetup(
                cfg.docker, cfg.agent_teams.config_dir
            )
        case "git":
            if cfg.dataset == "swebench":
                workspace_setup = GitWorkspaceSetup(
                    cfg.evals_workdir, cfg.git_clone_timeout_seconds
                )
                patch_extractor = PatchExtractor()

    # Scorer
    match cfg.scorer:
        case "swebench_docker":
            scorer = SWEBenchDockerScorer(
                client=_load_docker_module().from_env(),
                patch_pass_threshold=cfg.swebench_pass_threshold,
            )
            if patch_extractor is None and workspace_setup is not None:
                patch_extractor = PatchExtractor()
        case "swebench":
            scorer = SWEBenchScorer(cfg.swebench_pass_threshold)
            if patch_extractor is None and workspace_setup is not None:
                patch_extractor = PatchExtractor()
        case "regex":
            scorer = RegexScorer()
        case "event_status":
            scorer = EventStatusScorer()
        case _:
            scorer = KeywordScorer()

    # Load dataset
    if cfg.dataset == "swebench":
        loader = SWEBenchLoader()
    else:
        loader = JsonlLoader(dataset_name=cfg.dataset)

    items = loader.load(cfg.dataset_path)
    typer.echo(f"Loaded {len(items)} items from {cfg.dataset_path}")

    if cfg.item_ids:
        id_set = set(cfg.item_ids)
        items = [it for it in items if it.item_id in id_set]
        typer.echo(f"Filtered to {len(items)} items by item_ids")

    if cfg.limit is not None:
        items = items[: cfg.limit]
        typer.echo(f"Limited to {len(items)} items")

    try:
        _validate_unique_item_ids(items)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    ordered_item_ids = tuple(item.item_id for item in items)
    checkpoint_store = EvalCheckpointStore(cfg.output_dir)
    checkpoint_signature = build_checkpoint_signature(
        cfg,
        dataset_path=cfg.dataset_path,
        item_ids=ordered_item_ids,
    )

    if restart:
        archived_path = archive_output_dir(cfg.output_dir)
        if archived_path is not None:
            typer.echo(f"Archived previous output_dir to {archived_path}")
    try:
        if restart:
            checkpoint_store.ensure_initialized(checkpoint_signature)
            results_by_item_id: dict[str, EvalResult] = {}
        else:
            results_by_item_id = _load_checkpoint_results(
                cfg=cfg,
                signature=checkpoint_signature,
                rerun_item_ids=tuple(normalized_item_ids) if rerun else (),
            )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    report_item_ids = ordered_item_ids
    completed_item_ids = set(results_by_item_id)
    items_to_run = [item for item in items if item.item_id not in completed_item_ids]
    if rerun and results_by_item_id:
        existing_meta = checkpoint_store.load_meta()
        if existing_meta is not None:
            report_item_ids = existing_meta.signature.item_ids
        items_to_run = list(items)
    if results_by_item_id and not rerun:
        typer.echo(
            f"Resuming from checkpoint: {len(results_by_item_id)} completed, "
            f"{len(items_to_run)} remaining"
        )
    elif rerun:
        typer.echo(
            f"Rerunning {len(items_to_run)} item(s) against existing results in {cfg.output_dir}"
        )

    artifact_collector = (
        ArtifactCollector(cfg.output_dir) if cfg.save_artifacts else None
    )

    runner = EvalRunner(
        backend=backend,
        scorer=scorer,
        workspace_setup=workspace_setup,
        patch_extractor=patch_extractor,
        artifact_collector=artifact_collector,
        keep_workspaces=cfg.keep_workspaces,
        concurrency=cfg.concurrency,
        infra_retry_attempts=cfg.infra_retry_attempts,
        infra_retry_backoff_seconds=cfg.infra_retry_backoff_seconds,
    )

    total = len(items_to_run)
    typer.echo(
        f"Running {total} items (concurrency={cfg.concurrency}) "
        f"[remaining={len(items_to_run)}] ..."
    )
    completed = 0
    reporter = EvalReporter()

    def _format_usage_for_progress(result: object) -> str:
        from agent_teams_evals.models import EvalResult as _EvalResult

        assert isinstance(result, _EvalResult)
        in_k = result.token_usage.input_tokens / 1000
        cached_k = result.token_usage.cached_input_tokens / 1000
        out_k = result.token_usage.output_tokens / 1000
        reasoning_k = result.token_usage.reasoning_output_tokens / 1000
        return (
            f"input:{in_k:.1f}k cached:{cached_k:.1f}k "
            f"output:{out_k:.1f}k reasoning:{reasoning_k:.1f}k "
            f"requests:{result.token_usage.total_requests} "
            f"tool_calls:{result.token_usage.total_tool_calls}"
        )

    def _print_result(result: object) -> None:
        from agent_teams_evals.models import EvalResult as _EvalResult

        assert isinstance(result, _EvalResult)
        nonlocal completed
        completed += 1
        status = "PASS" if result.passed else "FAIL"
        typer.echo(
            f"[{completed}/{total}] {result.item_id}  {status}"
            f"  score={result.score:.3f}"
            f"  usage={_format_usage_for_progress(result)}"
            f"  dur={result.duration_seconds:.1f}s"
            f"  {result.scorer_detail}"
        )
        if result.error:
            typer.echo(f"  error: {result.error}")
        if result.build_log_path:
            typer.echo(f"  build_log: {result.build_log_path}")

    try:
        if cfg.concurrency <= 1:
            for item in items_to_run:
                result = runner.run_item(item)
                checkpoint_store.append_result(result)
                results_by_item_id[result.item_id] = result
                report_snapshot = _build_report_snapshot(
                    cfg=cfg,
                    scorer_name=scorer.name,
                    item_ids=report_item_ids,
                    results_by_item_id=results_by_item_id,
                )
                _write_report_snapshot(
                    cfg=cfg,
                    reporter=reporter,
                    report=report_snapshot,
                )
                _print_result(result)
        else:
            with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
                futures = {
                    pool.submit(runner.run_item, item): item for item in items_to_run
                }
                for future in as_completed(futures):
                    result = future.result()
                    checkpoint_store.append_result(result)
                    results_by_item_id[result.item_id] = result
                    report_snapshot = _build_report_snapshot(
                        cfg=cfg,
                        scorer_name=scorer.name,
                        item_ids=report_item_ids,
                        results_by_item_id=results_by_item_id,
                    )
                    _write_report_snapshot(
                        cfg=cfg,
                        reporter=reporter,
                        report=report_snapshot,
                    )
                    _print_result(result)
    finally:
        if workspace_setup is not None:
            workspace_setup.teardown()

    report = _build_report_snapshot(
        cfg=cfg,
        scorer_name=scorer.name,
        item_ids=report_item_ids,
        results_by_item_id=results_by_item_id,
    )
    reporter.print_summary(report)
    _write_report_snapshot(cfg=cfg, reporter=reporter, report=report)
    json_path = cfg.output_dir / "report.json"
    typer.echo(f"JSON report: {json_path}")
    if cfg.report_format in ("html", "both"):
        html_path = cfg.output_dir / "report.html"
        typer.echo(f"HTML report: {html_path}")


@app.command(name="init-config")
def init_config(
    output: Path = typer.Option(
        Path("eval.yaml"), help="Output path for sample config"
    ),
) -> None:
    """Generate a sample YAML run config."""
    from agent_teams_evals.run_config import sample_yaml

    output.write_text(sample_yaml(), encoding="utf-8")
    typer.echo(f"Sample config written to: {output}")
    typer.echo(f"Edit it, then run:  agent-teams-evals run --config {output}")


@app.command()
def report(
    results_file: Path = typer.Option(..., help="Path to JSON report file"),
    format: str = typer.Option("html", help="Output format: html | json | both"),
    output_file: Path | None = typer.Option(None, help="Output file path"),
) -> None:
    import json as _json

    from agent_teams_evals.models import EvalReport
    from agent_teams_evals.reporter import EvalReporter

    raw = results_file.read_text(encoding="utf-8")
    report_obj = EvalReport.model_validate(_json.loads(raw))
    reporter = EvalReporter()
    reporter.print_summary(report_obj)

    if output_file is None:
        suffix = ".html" if format == "html" else ".json"
        output_file = results_file.with_suffix(suffix)

    if format in ("html", "both"):
        html_path = (
            output_file if format == "html" else output_file.with_suffix(".html")
        )
        reporter.write_html(report_obj, html_path)
        typer.echo(f"HTML report: {html_path}")
    if format in ("json", "both"):
        json_path = (
            output_file if format == "json" else output_file.with_suffix(".json")
        )
        reporter.write_json(report_obj, json_path)
        typer.echo(f"JSON report: {json_path}")


if __name__ == "__main__":
    app()
