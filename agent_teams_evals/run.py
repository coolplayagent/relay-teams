from __future__ import annotations

import sys
from pathlib import Path

# When run as a standalone script, the project root is not automatically in
# sys.path.  Inserting it at position 0 ensures agent_teams_evals is found
# before any same-named package that may be installed in site-packages.
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import typer  # noqa: E402

app = typer.Typer(help="Agent benchmark evaluation CLI", add_completion=False)


@app.command()
def run(
    dataset: str = typer.Option(..., help="Dataset type: 'jsonl' or 'swebench'"),
    dataset_path: Path = typer.Option(..., help="Path to JSONL dataset file"),
    scorer: str = typer.Option(
        "keyword", help="Scorer: keyword | regex | event_status | swebench"
    ),
    base_url: str = typer.Option("http://127.0.0.1:8000", help="Backend base URL"),
    workspace_id: str = typer.Option("default", help="Workspace ID for sessions"),
    execution_mode: str = typer.Option("ai", help="Run execution mode"),
    run_timeout: float = typer.Option(300.0, help="Run timeout in seconds"),
    limit: int | None = typer.Option(None, help="Max items to evaluate"),
    item_ids: list[str] = typer.Option([], help="Specific item IDs to evaluate"),
    keep_workspaces: bool = typer.Option(False, help="Keep cloned repos after run"),
    concurrency: int = typer.Option(1, help="Number of items to run in parallel"),
    output_dir: Path = typer.Option(
        Path(".agent_teams/evals/results"), help="Output directory"
    ),
    swebench_threshold: float = typer.Option(0.8, help="SWE-bench pass threshold"),
    report_format: str = typer.Option("json", help="Report format: json | html | both"),
) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from agent_teams_evals.config import EvalConfig
    from agent_teams_evals.loaders.jsonl_loader import JsonlLoader
    from agent_teams_evals.loaders.swebench_loader import SWEBenchLoader
    from agent_teams_evals.reporter import EvalReporter, build_report
    from agent_teams_evals.runner import EvalRunner
    from agent_teams_evals.scorers.event_status_scorer import EventStatusScorer
    from agent_teams_evals.scorers.keyword_scorer import KeywordScorer
    from agent_teams_evals.scorers.regex_scorer import RegexScorer
    from agent_teams_evals.scorers.swebench_scorer import SWEBenchScorer
    from agent_teams_evals.workspace.git_setup import GitWorkspaceSetup
    from agent_teams_evals.workspace.patch_extractor import PatchExtractor

    config = EvalConfig(
        base_url=base_url,
        workspace_id=workspace_id,
        execution_mode=execution_mode,
        run_timeout_seconds=run_timeout,
        output_dir=output_dir,
        dataset_path=dataset_path,
        limit=limit,
        item_ids=tuple(item_ids),
        concurrency=concurrency,
        keep_workspaces=keep_workspaces,
        swebench_pass_threshold=swebench_threshold,
    )

    if dataset == "swebench":
        loader = SWEBenchLoader()
    else:
        loader = JsonlLoader(dataset_name=dataset)

    items = loader.load(dataset_path)
    typer.echo(f"Loaded {len(items)} items from {dataset_path}")

    if item_ids:
        id_set = set(item_ids)
        items = [it for it in items if it.item_id in id_set]
        typer.echo(f"Filtered to {len(items)} items by item_ids")

    if limit is not None:
        items = items[:limit]
        typer.echo(f"Limited to {len(items)} items")

    if dataset == "swebench" or scorer == "swebench":
        scorer_instance = SWEBenchScorer(config)
        workspace_setup: GitWorkspaceSetup | None = (
            GitWorkspaceSetup(config) if dataset == "swebench" else None
        )
        patch_ext: PatchExtractor | None = (
            PatchExtractor() if workspace_setup is not None else None
        )
    elif scorer == "regex":
        scorer_instance = RegexScorer()
        workspace_setup = None
        patch_ext = None
    elif scorer == "event_status":
        scorer_instance = EventStatusScorer()
        workspace_setup = None
        patch_ext = None
    else:
        scorer_instance = KeywordScorer()
        workspace_setup = None
        patch_ext = None

    runner = EvalRunner(
        config=config,
        scorer=scorer_instance,
        workspace_setup=workspace_setup,
        patch_extractor=patch_ext,
    )

    total = len(items)
    typer.echo(f"Running {total} items (concurrency={concurrency}) ...")
    results: list = []
    completed = 0

    def _print_result(result: object) -> None:
        from agent_teams_evals.models import EvalResult as _EvalResult

        assert isinstance(result, _EvalResult)
        nonlocal completed
        completed += 1
        status = "PASS" if result.passed else "FAIL"
        typer.echo(
            f"[{completed}/{total}] {result.item_id}  {status}"
            f"  score={result.score:.3f}  {result.scorer_detail}"
        )
        if result.error:
            typer.echo(f"  error: {result.error}")

    if concurrency <= 1:
        for item in items:
            result = runner.run_item(item)
            _print_result(result)
            results.append(result)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(runner.run_item, item): item for item in items}
            for future in as_completed(futures):
                result = future.result()
                _print_result(result)
                results.append(result)

    report = build_report(results, dataset=dataset, scorer_name=scorer_instance.name)
    reporter = EvalReporter()
    reporter.print_summary(report)

    output_dir.mkdir(parents=True, exist_ok=True)
    if report_format in ("json", "both"):
        json_path = output_dir / "report.json"
        reporter.write_json(report, json_path)
        typer.echo(f"JSON report: {json_path}")
    if report_format in ("html", "both"):
        html_path = output_dir / "report.html"
        reporter.write_html(report, html_path)
        typer.echo(f"HTML report: {html_path}")


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
