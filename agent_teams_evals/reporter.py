from __future__ import annotations

from pathlib import Path

import typer

from agent_teams_evals.models import EvalReport, EvalResult

_COL_WIDTHS = (30, 12, 8, 8, 10, 8)
_HEADERS = ("item_id", "outcome", "passed", "score", "duration(s)", "scorer")


def _row(result: EvalResult) -> tuple[str, ...]:
    return (
        result.item_id[:30],
        result.outcome.value,
        "PASS" if result.passed else "FAIL",
        f"{result.score:.3f}",
        f"{result.duration_seconds:.1f}",
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
            f"Tokens  : in={report.total_input_tokens}, "
            f"out={report.total_output_tokens}"
        )
        typer.echo(f"Mean dur: {report.mean_duration_seconds:.1f}s")
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
            rows_html += (
                f"<tr class='{status_class}'>"
                f"<td>{r.item_id}</td>"
                f"<td>{r.outcome.value}</td>"
                f"<td>{'PASS' if r.passed else 'FAIL'}</td>"
                f"<td>{r.score:.3f}</td>"
                f"<td>{r.duration_seconds:.1f}</td>"
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
<p>Mean score: {report.mean_score:.3f}</p>
<p>Mean duration: {report.mean_duration_seconds:.1f}s</p>
<p>Tokens: in={report.total_input_tokens}, out={report.total_output_tokens}</p>
<p>Generated: {report.generated_at.isoformat()}</p>
</div>
<table>
<thead>
<tr>
<th>item_id</th><th>outcome</th><th>passed</th><th>score</th>
<th>duration(s)</th><th>scorer</th><th>detail</th><th>error</th>
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
) -> EvalReport:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    errored = sum(1 for r in results if r.error is not None)
    failed = total - passed
    pass_rate = passed / total if total > 0 else 0.0
    mean_score = sum(r.score for r in results) / total if total > 0 else 0.0
    mean_duration = (
        sum(r.duration_seconds for r in results) / total if total > 0 else 0.0
    )
    total_input = sum(r.token_usage.input_tokens for r in results)
    total_output = sum(r.token_usage.output_tokens for r in results)

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
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        results=tuple(results),
    )
