# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from relay_teams.agents.tasks.enums import TaskTimeoutAction, WakeupStatus
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry


def _build_wakeup_entries(count: int) -> tuple[AgentWakeupEntry, ...]:
    now = datetime.now(tz=timezone.utc)
    return tuple(
        AgentWakeupEntry(
            wakeup_id=f"wake-{i}",
            task_id=f"task-{i % 50}",
            trace_id="bench-trace",
            session_id="bench-session",
            coalesce_key=f"coalesce-{i % 50}",
            timeout_action=TaskTimeoutAction.RETRY,
            timeout_seconds=300.0,
            attempt=1,
            max_attempts=3,
            status=WakeupStatus.PENDING,
            enqueued_at=now,
        )
        for i in range(count)
    )


def _coalesce(entries: tuple[AgentWakeupEntry, ...]) -> dict[str, AgentWakeupEntry]:
    """Simulate coalesce-and-enqueue: deduplicate by coalesce_key, keep latest."""
    deduped: dict[str, AgentWakeupEntry] = {}
    for entry in entries:
        existing = deduped.get(entry.coalesce_key)
        if existing is None or entry.enqueued_at >= existing.enqueued_at:
            deduped[entry.coalesce_key] = entry
    return deduped


def test_micro_wakeup_build_100(benchmark):
    result = benchmark(_build_wakeup_entries, 100)
    assert len(result) == 100


def test_micro_wakeup_build_1000(benchmark):
    result = benchmark(_build_wakeup_entries, 1000)
    assert len(result) == 1000


def test_micro_wakeup_coalesce_100(benchmark):
    entries = _build_wakeup_entries(100)
    _ = benchmark(_coalesce, entries)


def test_micro_wakeup_coalesce_1000(benchmark):
    entries = _build_wakeup_entries(1000)
    result = benchmark(_coalesce, entries)
    assert len(result) <= 50  # 50 unique coalesce keys


def test_micro_wakeup_serialization(benchmark):
    entries = _build_wakeup_entries(100)

    def _roundtrip() -> tuple[AgentWakeupEntry, ...]:
        import json

        data = "[" + ",".join(e.model_dump_json() for e in entries) + "]"
        raw = json.loads(data)
        return tuple(AgentWakeupEntry.model_validate(r) for r in raw)

    result = benchmark(_roundtrip)
    assert len(result) == 100
