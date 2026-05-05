# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from relay_teams.logger import get_logger

from benchmarks.swebench.config import (
    SWEBenchConfig,
    SWEBenchInstanceResult,
    SWEBenchRunResult,
)

logger = get_logger(__name__)


class SWEBenchRunner:
    """SWE-bench continuous tracking runner.

    Loads SWE-bench dataset instances, runs relay-teams against each,
    collects patch results, and produces a SWEBenchRunResult report.

    Actual relay-teams execution requires a live environment; the runner
    provides the orchestration scaffold. Individual instance execution
    delegates to ``_run_instance`` which should be overridden or wrapped
    for full end-to-end runs.
    """

    async def run(self, config: SWEBenchConfig) -> SWEBenchRunResult:
        run_start = datetime.now(tz=timezone.utc)
        instances = self._load_instances(config)
        limited = instances[: config.max_instances]

        results: list[SWEBenchInstanceResult] = []
        for instance in limited:
            try:
                result = await self._run_instance(instance, config)
            except Exception as exc:
                logger.warning(
                    "swebench instance %s failed: %s",
                    instance.get("instance_id", "unknown"),
                    exc,
                )
                result = SWEBenchInstanceResult(
                    instance_id=str(instance.get("instance_id", "unknown")),
                    repo=str(instance.get("repo", "")),
                    resolved=False,
                    patch_applied=False,
                    tests_passed=0,
                    tests_failed=0,
                    duration_seconds=0.0,
                    error_message=str(exc),
                )
            results.append(result)

        run_end = datetime.now(tz=timezone.utc)
        resolved_count = sum(1 for r in results if r.resolved)
        total = len(results)

        return SWEBenchRunResult(
            run_id=uuid4().hex[:12],
            timestamp=run_start,
            config=config,
            instances=tuple(results),
            resolved_count=resolved_count,
            total_count=total,
            resolve_rate=resolved_count / max(total, 1),
            total_duration_seconds=(run_end - run_start).total_seconds(),
        )

    @staticmethod
    def _load_instances(config: SWEBenchConfig) -> list[dict[str, object]]:
        """Load SWE-bench instances from the dataset path.

        Supports JSONL format (one JSON object per line).
        Returns an empty list if the file does not exist.
        """
        import json

        if not config.dataset_path.exists():
            logger.warning("swebench dataset not found: %s", config.dataset_path)
            return []

        instances: list[dict[str, object]] = []
        with config.dataset_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                instances.append(json.loads(line))
        return instances

    @staticmethod
    async def _run_instance(
        instance: dict[str, object],
        config: SWEBenchConfig,
    ) -> SWEBenchInstanceResult:
        """Run a single SWE-bench instance through relay-teams.

        This is a scaffold implementation. Full execution requires
        a relay-teams server endpoint and worker pool.
        """
        instance_id = str(instance.get("instance_id", "unknown"))
        repo = str(instance.get("repo", ""))
        logger.info(
            "swebench running instance: %s (parallel_workers=%s)",
            instance_id,
            config.parallel_workers,
        )

        # Placeholder: actual execution would invoke relay-teams here
        return SWEBenchInstanceResult(
            instance_id=instance_id,
            repo=repo,
            resolved=False,
            patch_applied=False,
            tests_passed=0,
            tests_failed=0,
            duration_seconds=0.0,
            error_message="placeholder: relay-teams execution not wired",
        )
