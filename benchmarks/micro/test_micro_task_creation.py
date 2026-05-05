# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.tasks.models import TaskEnvelope


def _parse_tasks(data: list[dict[str, object]]) -> list[TaskEnvelope]:
    return [TaskEnvelope.model_validate(d) for d in data]


def _resolve_dependency_depth(envelopes: list[TaskEnvelope]) -> dict[str, int]:
    task_map = {e.task_id: e for e in envelopes}
    cache: dict[str, int] = {}

    def _depth(tid: str) -> int:
        if tid in cache:
            return cache[tid]
        env = task_map.get(tid)
        if env is None or not env.depends_on_task_ids:
            cache[tid] = 0
            return 0
        d = 1 + max(_depth(dep) for dep in env.depends_on_task_ids)
        cache[tid] = d
        return d

    for e in envelopes:
        _depth(e.task_id)
    return cache


def test_micro_task_creation_10(benchmark, task_data_10):
    result = benchmark(_parse_tasks, task_data_10)
    assert len(result) == 10


def test_micro_task_creation_50(benchmark, task_data_50):
    result = benchmark(_parse_tasks, task_data_50)
    assert len(result) == 50


def test_micro_task_creation_100(benchmark, task_data_100):
    result = benchmark(_parse_tasks, task_data_100)
    assert len(result) == 100


def test_micro_dependency_depth_resolution_10(benchmark, task_data_10):
    envelopes = _parse_tasks(task_data_10)
    result = benchmark(_resolve_dependency_depth, envelopes)
    assert len(result) == 10


def test_micro_dependency_depth_resolution_100(benchmark, task_data_100):
    envelopes = _parse_tasks(task_data_100)
    result = benchmark(_resolve_dependency_depth, envelopes)
    assert len(result) == 100
