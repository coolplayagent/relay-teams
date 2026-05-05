# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from relay_teams.logger import get_logger

from benchmarks.swebench.config import SWEBenchRunResult

logger = get_logger(__name__)


def generate_report(
    result: SWEBenchRunResult,
    output_dir: Path,
) -> Path:
    """Persist a SWEBenchRunResult as a JSON file and return the path.

    Output format::

        {output_dir}/{run_id}_{timestamp}.json
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = result.timestamp.strftime("%Y%m%dT%H%M%S")
    filename = f"{result.run_id}_{ts}.json"
    path = output_dir / filename

    payload = result.model_dump(mode="json")
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    logger.info("swebench report written: %s", path)
    return path


def print_summary(result: SWEBenchRunResult) -> None:
    """Print a human-readable summary to the logger."""
    logger.info(
        "swebench run %s: %d/%d resolved (%.1f%%) in %.1fs",
        result.run_id,
        result.resolved_count,
        result.total_count,
        result.resolve_rate * 100,
        result.total_duration_seconds,
    )
    for inst in result.instances:
        status = "RESOLVED" if inst.resolved else "FAILED"
        logger.info(
            "  [%s] %s (%s) - %ds",
            status,
            inst.instance_id,
            inst.repo,
            int(inst.duration_seconds),
        )
