"""Download SWE-bench Verified dataset items to a local JSONL file.

Usage:
    uv run python src/agent_teams_evals/scripts/download_swebench.py --limit 10
    uv run python src/agent_teams_evals/scripts/download_swebench.py --limit 500 --output .agent_teams/evals/datasets/swebench-verified-500.jsonl
    uv run python src/agent_teams_evals/scripts/download_swebench.py --ids astropy__astropy-12907 astropy__astropy-13033
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import import_module
import json
from pathlib import Path
from typing import Protocol, cast

import typer

app = typer.Typer(add_completion=False)

type DatasetRow = Mapping[str, object]


class _DatasetsModule(Protocol):
    def load_dataset(
        self,
        path: str,
        *,
        split: str,
        streaming: bool = False,
    ) -> Iterable[DatasetRow]: ...


def _load_dataset(
    path: str,
    *,
    split: str,
    streaming: bool = False,
) -> Iterable[DatasetRow]:
    datasets_module = cast(_DatasetsModule, import_module("datasets"))
    return datasets_module.load_dataset(path, split=split, streaming=streaming)


@app.command()
def main(
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output JSONL path. Defaults to .agent_teams/evals/datasets/swebench-verified-{N}.jsonl",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-n",
        help="Maximum number of items to download. Omit to download all.",
    ),
    ids: list[str] = typer.Option(
        [],
        "--ids",
        help="Download only these instance IDs (repeatable). Overrides --limit.",
    ),
    dataset_name: str = typer.Option(
        "SWE-bench/SWE-bench_Verified",
        "--dataset",
        help="HuggingFace dataset name.",
    ),
    split: str = typer.Option("test", "--split", help="Dataset split to use."),
) -> None:
    id_set = set(ids)

    typer.echo(f"Loading {dataset_name} (split={split}, streaming) ...")
    ds = _load_dataset(dataset_name, split=split, streaming=True)

    items: list[dict[str, object]] = []
    for row in ds:
        item = dict(row)
        instance_id = str(item["instance_id"])
        if id_set and instance_id not in id_set:
            continue
        items.append(item)
        if id_set and len(items) >= len(id_set):
            break
        if not id_set and limit is not None and len(items) >= limit:
            break

    if output is None:
        n = len(items)
        output = Path(f".agent_teams/evals/datasets/swebench-verified-{n}.jsonl")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    typer.echo(f"Wrote {len(items)} items to {output}")
    for item in items:
        typer.echo(f"  {item['instance_id']}")


if __name__ == "__main__":
    app()
