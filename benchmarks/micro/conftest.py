# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from relay_teams.agents.tasks.enums import (
    TaskTimeoutAction,
    WakeupStatus,
)
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.agents.orchestration.graph_models import (
    OrchestrationGraph,
    OrchestrationGraphEdge,
    OrchestrationGraphNode,
)
from relay_teams.memory.models import (
    MemoryContent,
    MemoryEntry,
    MemoryEntryKind,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_role_definition(index: int) -> dict[str, object]:
    return {
        "role_id": f"role-{index}",
        "name": f"Test Role {index}",
        "description": f"Description for role {index}",
        "version": "1.0",
        "system_prompt": f"You are test role {index}.",
    }


def _make_task_envelope(
    index: int, depends_on: tuple[str, ...] = ()
) -> dict[str, object]:
    return {
        "task_id": f"task-{index}",
        "session_id": "bench-session",
        "trace_id": "bench-trace",
        "role_id": f"role-{index % 5}",
        "objective": f"Objective for task {index}",
        "depends_on_task_ids": depends_on,
        "verification": {"checklist": ("non_empty_response",)},
    }


def _make_graph_node(index: int) -> OrchestrationGraphNode:
    return OrchestrationGraphNode(
        node_id=f"node-{index}",
        role_id=f"role-{index % 5}",
        objective=f"Execute node {index}",
    )


def _make_memory_entry(
    index: int, tier: MemoryTier = MemoryTier.WORKING
) -> MemoryEntry:
    now = datetime.now(tz=timezone.utc)
    return MemoryEntry(
        id=f"mem-{index}",
        tier=tier,
        scope=MemoryScope.WORKSPACE,
        workspace_id="ws-bench",
        run_id="run-bench" if tier == MemoryTier.WORKING else None,
        kind=MemoryEntryKind.FACT,
        content=MemoryContent(
            title=f"Memory entry {index}",
            body=f"This is the body content for memory entry {index}. " * 3,
        ),
        source=MemorySourceKind.MANUAL,
        created_at=now,
        updated_at=now,
    )


def _make_wakeup_entry(index: int) -> AgentWakeupEntry:
    return AgentWakeupEntry(
        wakeup_id=f"wake-{index}",
        task_id=f"task-{index}",
        trace_id="bench-trace",
        session_id="bench-session",
        coalesce_key=f"coalesce-{index}",
        timeout_action=TaskTimeoutAction.RETRY,
        timeout_seconds=300.0,
        attempt=1,
        max_attempts=3,
        status=WakeupStatus.PENDING,
        enqueued_at=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def role_json_data_100() -> list[dict[str, object]]:
    return [_make_role_definition(i) for i in range(100)]


@pytest.fixture
def role_json_data_500() -> list[dict[str, object]]:
    return [_make_role_definition(i) for i in range(500)]


@pytest.fixture
def role_json_data_1000() -> list[dict[str, object]]:
    return [_make_role_definition(i) for i in range(1000)]


@pytest.fixture
def task_data_10() -> list[dict[str, object]]:
    data: list[dict[str, object]] = []
    for i in range(10):
        deps = tuple(f"task-{j}" for j in range(max(0, i - 2), i))
        data.append(_make_task_envelope(i, depends_on=deps))
    return data


@pytest.fixture
def task_data_50() -> list[dict[str, object]]:
    data: list[dict[str, object]] = []
    for i in range(50):
        deps = tuple(f"task-{j}" for j in range(max(0, i - 3), i))
        data.append(_make_task_envelope(i, depends_on=deps))
    return data


@pytest.fixture
def task_data_100() -> list[dict[str, object]]:
    data: list[dict[str, object]] = []
    for i in range(100):
        deps = tuple(f"task-{j}" for j in range(max(0, i - 4), i))
        data.append(_make_task_envelope(i, depends_on=deps))
    return data


@pytest.fixture
def graph_10_nodes() -> OrchestrationGraph:
    nodes = [_make_graph_node(i) for i in range(10)]
    edges: list[OrchestrationGraphEdge] = []
    for i in range(1, 10):
        edges.append(
            OrchestrationGraphEdge(
                from_node_id=f"node-{i - 1}",
                to_node_id=f"node-{i}",
            )
        )
    return OrchestrationGraph(nodes=tuple(nodes), edges=tuple(edges))


@pytest.fixture
def graph_50_nodes() -> OrchestrationGraph:
    nodes = [_make_graph_node(i) for i in range(50)]
    edges: list[OrchestrationGraphEdge] = []
    for i in range(1, 50):
        edges.append(
            OrchestrationGraphEdge(
                from_node_id=f"node-{i - 1}",
                to_node_id=f"node-{i}",
            )
        )
    return OrchestrationGraph(nodes=tuple(nodes), edges=tuple(edges))


@pytest.fixture
def graph_100_nodes() -> OrchestrationGraph:
    nodes = [_make_graph_node(i) for i in range(100)]
    edges: list[OrchestrationGraphEdge] = []
    # Fan-out from first node
    for i in range(1, 20):
        edges.append(
            OrchestrationGraphEdge(
                from_node_id="node-0",
                to_node_id=f"node-{i}",
            )
        )
    # Chain for remaining
    for i in range(20, 100):
        edges.append(
            OrchestrationGraphEdge(
                from_node_id=f"node-{i - 1}",
                to_node_id=f"node-{i}",
            )
        )
    return OrchestrationGraph(nodes=tuple(nodes), edges=tuple(edges))


@pytest.fixture
def memory_entries_100() -> list[MemoryEntry]:
    return [_make_memory_entry(i) for i in range(100)]


@pytest.fixture
def memory_entries_1000() -> list[MemoryEntry]:
    return [_make_memory_entry(i) for i in range(1000)]
