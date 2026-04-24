# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.orchestration.task_execution_service import (
    TASK_MEMORY_RESULT_EXCERPT_CHARS,
    _truncate_task_memory_result,
)


def test_truncate_task_memory_result_limits_long_normalized_results() -> None:
    result = "alpha\n" + ("x" * (TASK_MEMORY_RESULT_EXCERPT_CHARS + 10))

    truncated = _truncate_task_memory_result(result)

    assert len(truncated) == TASK_MEMORY_RESULT_EXCERPT_CHARS + 3
    assert truncated.endswith("...")
    assert "\n" not in truncated
