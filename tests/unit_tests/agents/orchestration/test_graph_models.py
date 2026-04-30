# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.orchestration.graph_models import OrchestrationGraph
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
