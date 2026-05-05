# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.orchestration.graph_models import OrchestrationGraph


def _topo_sort(graph: OrchestrationGraph) -> tuple[str, ...]:
    return graph.topological_node_ids()


def test_micro_graph_topology_10(benchmark, graph_10_nodes):
    result = benchmark(_topo_sort, graph_10_nodes)
    assert len(result) == 10


def test_micro_graph_topology_50(benchmark, graph_50_nodes):
    result = benchmark(_topo_sort, graph_50_nodes)
    assert len(result) == 50


def test_micro_graph_topology_100(benchmark, graph_100_nodes):
    result = benchmark(_topo_sort, graph_100_nodes)
    assert len(result) == 100


def test_micro_graph_upstream_lookup_100(benchmark, graph_100_nodes):
    def _lookup_all_upstream() -> tuple[tuple[str, ...], ...]:
        return tuple(graph_100_nodes.upstream_node_ids(f"node-{i}") for i in range(100))

    result = benchmark(_lookup_all_upstream)
    assert len(result) == 100


def test_micro_graph_serialization_roundtrip(benchmark, graph_100_nodes):
    def _roundtrip() -> OrchestrationGraph:
        data = graph_100_nodes.model_dump_json()
        return OrchestrationGraph.model_validate_json(data)

    result = benchmark(_roundtrip)
    assert len(result.nodes) == 100
