from __future__ import annotations

import statistics
from pathlib import Path

import typer

from agent_teams_evals.models import EvalReport, EvalResult, RunOutcome

_COL_WIDTHS = (30, 12, 8, 8, 10, 14, 8)
_HEADERS = (
    "item_id",
    "outcome",
    "passed",
    "score",
    "duration(s)",
    "tok_in/out(k)",
    "scorer",
)


def _row(result: EvalResult) -> tuple[str, ...]:
    in_k = result.token_usage.input_tokens / 1000
    out_k = result.token_usage.output_tokens / 1000
    return (
        result.item_id[:30],
        result.outcome.value,
        "PASS" if result.passed else "FAIL",
        f"{result.score:.3f}",
        f"{result.duration_seconds:.1f}",
        f"{in_k:.1f}k/{out_k:.1f}k",
        result.scorer_name,
    )


def _hr(widths: tuple[int, ...]) -> str:
    return "+-" + "-+-".join("-" * w for w in widths) + "-+"


def _line(values: tuple[str, ...], widths: tuple[int, ...]) -> str:
    cells = " | ".join(v.ljust(w) for v, w in zip(values, widths))
    return f"| {cells} |"


class EvalReporter:
    def print_summary(self, report: EvalReport) -> None:
        hr = _hr(_COL_WIDTHS)
        typer.echo(f"\nDataset : {report.dataset}")
        typer.echo(f"Scorer  : {report.scorer_name}")
        typer.echo(
            f"Results : {report.passed}/{report.total} passed "
            f"({report.pass_rate * 100:.1f}%)"
        )
        typer.echo(
            f"Outcomes: completed={report.outcome_completed}"
            f"  failed={report.outcome_failed}"
            f"  timed_out={report.outcome_timed_out}"
            f"  stopped={report.outcome_stopped}"
        )
        typer.echo(
            f"Tokens  : in={report.total_input_tokens:,}"
            f"  out={report.total_output_tokens:,}"
            f"  est_cost=${report.estimated_cost_usd:.4f}"
        )
        typer.echo(
            f"Duration: mean={report.mean_duration_seconds:.1f}s"
            f"  p50={report.p50_duration_seconds:.1f}s"
            f"  p95={report.p95_duration_seconds:.1f}s"
        )
        typer.echo("")
        typer.echo(hr)
        typer.echo(_line(_HEADERS, _COL_WIDTHS))
        typer.echo(hr)
        for result in report.results:
            typer.echo(_line(_row(result), _COL_WIDTHS))
        typer.echo(hr)

    def write_json(self, report: EvalReport, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    def write_html(self, report: EvalReport, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows_html = ""
        for r in report.results:
            status_class = "pass" if r.passed else "fail"
            error_cell = r.error or ""
            in_k = r.token_usage.input_tokens / 1000
            out_k = r.token_usage.output_tokens / 1000
            rows_html += (
                f"<tr class='{status_class}'>"
                f"<td>{r.item_id}</td>"
                f"<td>{r.outcome.value}</td>"
                f"<td>{'PASS' if r.passed else 'FAIL'}</td>"
                f"<td>{r.score:.3f}</td>"
                f"<td>{r.duration_seconds:.1f}s</td>"
                f"<td>{in_k:.1f}k / {out_k:.1f}k</td>"
                f"<td>{r.scorer_name}</td>"
                f"<td>{r.scorer_detail}</td>"
                f"<td>{error_cell}</td>"
                "</tr>\n"
            )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Eval Report - {report.dataset}</title>
<style>
body {{ font-family: monospace; margin: 2em; }}
h1 {{ font-size: 1.2em; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; }}
th {{ background: #eee; }}
tr.pass td {{ background: #e6ffe6; }}
tr.fail td {{ background: #ffe6e6; }}
.summary {{ margin-bottom: 1em; }}
</style>
</head>
<body>
<h1>Eval Report</h1>
<div class="summary">
<p>Dataset: {report.dataset}</p>
<p>Scorer: {report.scorer_name}</p>
<p>Results: {report.passed}/{report.total} passed ({report.pass_rate * 100:.1f}%)</p>
<p>Outcomes: completed={report.outcome_completed} failed={report.outcome_failed} timed_out={report.outcome_timed_out} stopped={report.outcome_stopped}</p>
<p>Mean score: {report.mean_score:.3f}</p>
<p>Duration: mean={report.mean_duration_seconds:.1f}s p50={report.p50_duration_seconds:.1f}s p95={report.p95_duration_seconds:.1f}s</p>
<p>Tokens: in={report.total_input_tokens:,} out={report.total_output_tokens:,} est_cost=${report.estimated_cost_usd:.4f}</p>
<p>Generated: {report.generated_at.isoformat()}</p>
</div>
<table>
<thead>
<tr>
<th>item_id</th><th>outcome</th><th>passed</th><th>score</th>
<th>duration</th><th>tok_in/out(k)</th><th>scorer</th><th>detail</th><th>error</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>"""
        path.write_text(html, encoding="utf-8")


def build_report(
    results: list[EvalResult],
    dataset: str,
    scorer_name: str,
    *,
    cost_per_million_input: float = 3.0,
    cost_per_million_output: float = 15.0,
) -> EvalReport:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    errored = sum(1 for r in results if r.error is not None)
    failed = total - passed
    pass_rate = passed / total if total > 0 else 0.0
    mean_score = sum(r.score for r in results) / total if total > 0 else 0.0

    durations = sorted(r.duration_seconds for r in results)
    mean_duration = sum(durations) / total if total > 0 else 0.0
    p50 = statistics.median(durations) if durations else 0.0
    p95_idx = max(0, int(len(durations) * 0.95) - 1)
    p95 = durations[p95_idx] if durations else 0.0

    outcome_completed = sum(1 for r in results if r.outcome == RunOutcome.COMPLETED)
    outcome_failed = sum(1 for r in results if r.outcome == RunOutcome.FAILED)
    outcome_timed_out = sum(1 for r in results if r.outcome == RunOutcome.TIMEOUT)
    outcome_stopped = sum(1 for r in results if r.outcome == RunOutcome.STOPPED)

    total_input = sum(r.token_usage.input_tokens for r in results)
    total_output = sum(r.token_usage.output_tokens for r in results)
    estimated_cost = (
        total_input / 1_000_000 * cost_per_million_input
        + total_output / 1_000_000 * cost_per_million_output
    )

    return EvalReport(
        dataset=dataset,
        scorer_name=scorer_name,
        total=total,
        passed=passed,
        failed=failed,
        errored=errored,
        pass_rate=pass_rate,
        mean_score=mean_score,
        mean_duration_seconds=mean_duration,
        p50_duration_seconds=p50,
        p95_duration_seconds=p95,
        outcome_completed=outcome_completed,
        outcome_failed=outcome_failed,
        outcome_timed_out=outcome_timed_out,
        outcome_stopped=outcome_stopped,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        estimated_cost_usd=estimated_cost,
        results=tuple(results),
    )
