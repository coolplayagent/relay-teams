# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.orchestration.graph_models import (
    OrchestrationGraph,
    OrchestrationGraphEdge,
    OrchestrationGraphNode,
    build_orchestration_graph_prompt,
)
from relay_teams.agents.orchestration.settings_models import OrchestrationPreset


def test_orchestration_graph_orders_fanout_join_nodes() -> None:
    graph = OrchestrationGraph.model_validate(
        {
            "nodes": [
                {
                    "node_id": "explore_api",
                    "role_id": "Explorer",
                    "objective": "Map API.",
                },
                {
                    "node_id": "explore_tests",
                    "role_id": "Explorer",
                    "objective": "Map tests.",
                },
                {
                    "node_id": "implement",
                    "role_id": "Crafter",
                    "objective": "Patch code.",
                },
                {"node_id": "verify", "role_id": "Gater", "objective": "Verify code."},
            ],
            "edges": [
                {"from_node_id": "explore_api", "to_node_id": "implement"},
                {"from_node_id": "explore_tests", "to_node_id": "implement"},
                {"from_node_id": "implement", "to_node_id": "verify"},
            ],
        }
    )

    assert graph.topological_node_ids() == (
        "explore_api",
        "explore_tests",
        "implement",
        "verify",
    )
    assert graph.upstream_node_ids("implement") == (
        "explore_api",
        "explore_tests",
    )


def test_orchestration_graph_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="acyclic"):
        OrchestrationGraph.model_validate(
            {
                "nodes": [
                    {"node_id": "a", "role_id": "Writer", "objective": "A."},
                    {"node_id": "b", "role_id": "Reviewer", "objective": "B."},
                ],
                "edges": [
                    {"from_node_id": "a", "to_node_id": "b"},
                    {"from_node_id": "b", "to_node_id": "a"},
                ],
            }
        )


def test_orchestration_graph_normalizes_optional_text_fields() -> None:
    node = OrchestrationGraphNode.model_validate(
        {"node_id": "write", "role_id": "Writer", "objective": "Write.", "title": None}
    )
    edge = OrchestrationGraphEdge.model_validate(
        {
            "from_node_id": "write",
            "to_node_id": "review",
            "condition": None,
            "description": "  after write  ",
        }
    )

    assert node.title == ""
    assert edge.condition == ""
    assert edge.description == "after write"


def test_orchestration_graph_rejects_invalid_node_and_edge_references() -> None:
    with pytest.raises(ValueError, match="cannot target the same node"):
        OrchestrationGraphEdge.model_validate(
            {"from_node_id": "write", "to_node_id": "write"}
        )

    with pytest.raises(ValueError, match="node ids must be unique"):
        OrchestrationGraph.model_validate(
            {
                "nodes": [
                    {"node_id": "write", "role_id": "Writer", "objective": "Write."},
                    {"node_id": "write", "role_id": "Reviewer", "objective": "Review."},
                ],
            }
        )

    with pytest.raises(ValueError, match="unknown from_node_id"):
        OrchestrationGraph.model_validate(
            {
                "nodes": [
                    {"node_id": "review", "role_id": "Reviewer", "objective": "Review."}
                ],
                "edges": [{"from_node_id": "write", "to_node_id": "review"}],
            }
        )

    with pytest.raises(ValueError, match="unknown to_node_id"):
        OrchestrationGraph.model_validate(
            {
                "nodes": [
                    {"node_id": "write", "role_id": "Writer", "objective": "Write."}
                ],
                "edges": [{"from_node_id": "write", "to_node_id": "review"}],
            }
        )

    with pytest.raises(ValueError, match="edges must be unique"):
        OrchestrationGraph.model_validate(
            {
                "nodes": [
                    {"node_id": "write", "role_id": "Writer", "objective": "Write."},
                    {
                        "node_id": "review",
                        "role_id": "Reviewer",
                        "objective": "Review.",
                    },
                ],
                "edges": [
                    {"from_node_id": "write", "to_node_id": "review"},
                    {"from_node_id": "write", "to_node_id": "review"},
                ],
            }
        )

    with pytest.raises(ValueError, match="final_response_node_id must reference"):
        OrchestrationGraph.model_validate(
            {
                "final_response_node_id": "missing",
                "nodes": [
                    {"node_id": "write", "role_id": "Writer", "objective": "Write."}
                ],
            }
        )

    graph = OrchestrationGraph.model_validate(
        {"nodes": [{"node_id": "write", "role_id": "Writer", "objective": "Write."}]}
    )
    with pytest.raises(KeyError, match="Unknown orchestration graph node"):
        graph.node_by_id("missing")


def test_build_orchestration_graph_prompt_includes_nodes_edges_and_limits() -> None:
    graph = OrchestrationGraph.model_validate(
        {
            "max_parallel_tasks": 2,
            "final_response_node_id": "review",
            "nodes": [
                {
                    "node_id": "write",
                    "role_id": "Writer",
                    "title": "Draft",
                    "objective": "Write the patch.",
                },
                {
                    "node_id": "review",
                    "role_id": "Reviewer",
                    "objective": "Review the patch.",
                },
            ],
            "edges": [
                {
                    "from_node_id": "write",
                    "to_node_id": "review",
                    "condition": "draft passes tests",
                }
            ],
        }
    )

    prompt = build_orchestration_graph_prompt(graph)

    assert "## Orchestration Graph" in prompt
    assert "DAG template" in prompt
    assert "- write (Draft): role=Writer" in prompt
    assert "  objective=Write the patch." in prompt
    assert "- review: role=Reviewer" in prompt
    assert "- write -> review when draft passes tests" in prompt
    assert "Max parallel delegated tasks: 2" in prompt
    assert "Final response anchor node: review" in prompt


def test_orchestration_preset_rejects_graph_role_outside_allowed_roles() -> None:
    with pytest.raises(ValueError, match="role_id must be listed"):
        OrchestrationPreset.model_validate(
            {
                "preset_id": "graph",
                "name": "Graph",
                "role_ids": ["Writer"],
                "orchestration_prompt": "Run graph.",
                "graph": {
                    "nodes": [
                        {
                            "node_id": "review",
                            "role_id": "Reviewer",
                            "objective": "Review.",
                        }
                    ]
                },
            }
        )
