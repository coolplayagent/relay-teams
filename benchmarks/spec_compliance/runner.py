# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from benchmarks.spec_compliance.checks import DIRECTORY_CHECKS, FILE_CHECKS
from benchmarks.spec_compliance.models import (
    ComplianceCheckCategory,
    ComplianceCheckResult,
    ComplianceRunResult,
)

_SOURCE_ROOT = Path("src/relay_teams")


class SpecComplianceRunner:
    """Spec-compliance runner.

    Scans ``src/relay_teams/`` and applies 8 categories of coding
    standard checks derived from AGENTS.md.  Produces a
    ComplianceRunResult with per-file violation details and an
    overall score.
    """

    def __init__(self, source_root: Path = _SOURCE_ROOT) -> None:
        self._source_root = source_root

    def run(self) -> ComplianceRunResult:
        timestamp = datetime.now(tz=timezone.utc)
        py_files = sorted(self._source_root.rglob("*.py"))
        modules_checked = len(py_files)

        # Per-file violations
        violations_by_file: dict[str, tuple[ComplianceCheckResult, ...]] = {}

        # Aggregate check results by category
        category_results: dict[ComplianceCheckCategory, list[str]] = {
            cat: [] for cat in ComplianceCheckCategory
        }

        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            file_results: list[ComplianceCheckResult] = []
            for check_fn in FILE_CHECKS:
                result = check_fn(py_file, content)
                if not result.passed:
                    file_results.append(result)
                    category_results[result.category].extend(result.violations)
            if file_results:
                violations_by_file[str(py_file)] = tuple(file_results)

        # Directory-level checks
        for check_fn in DIRECTORY_CHECKS:
            result = check_fn(self._source_root)
            if not result.passed:
                violations_by_file[str(self._source_root)] = (
                    violations_by_file.get(str(self._source_root), ())
                ) + (result,)
                category_results[result.category].extend(result.violations)

        # Build aggregate ComplianceCheckResult per category
        all_checks: list[ComplianceCheckResult] = []
        for cat in tuple(ComplianceCheckCategory):
            cat_violations = category_results[cat]
            all_checks.append(
                ComplianceCheckResult(
                    category=cat,
                    passed=len(cat_violations) == 0,
                    violations=tuple(cat_violations),
                )
            )

        passed_count = sum(1 for c in all_checks if c.passed)
        total_count = len(all_checks)
        overall_score = passed_count / max(total_count, 1)

        return ComplianceRunResult(
            timestamp=timestamp,
            modules_checked=modules_checked,
            checks=tuple(all_checks),
            overall_score=overall_score,
            violations_by_file=violations_by_file,
        )


if __name__ == "__main__":
    runner = SpecComplianceRunner()
    result = runner.run()
    import json

    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
