# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import deque

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.agents.tasks.models import VerificationPlan
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class OrchestrationGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: RequiredIdentifierStr
    role_id: RequiredIdentifierStr
    objective: str = Field(min_length=1)
    title: str = ""
    verification: VerificationPlan = Field(default_factory=VerificationPlan)

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


class OrchestrationGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_node_id: RequiredIdentifierStr
    to_node_id: RequiredIdentifierStr
    condition: str = ""
    description: str = ""

    @field_validator("condition", "description", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @model_validator(mode="after")
    def validate_not_self_loop(self) -> OrchestrationGraphEdge:
        if self.from_node_id == self.to_node_id:
            raise ValueError("orchestration graph edge cannot target the same node")
        return self


class OrchestrationGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: tuple[OrchestrationGraphNode, ...] = Field(min_length=1)
    edges: tuple[OrchestrationGraphEdge, ...] = ()
    max_parallel_tasks: int = Field(default=4, ge=1, le=16)
    final_response_node_id: OptionalIdentifierStr = None

    @model_validator(mode="after")
    def validate_graph(self) -> OrchestrationGraph:
        node_ids = tuple(node.node_id for node in self.nodes)
        node_id_set = set(node_ids)
        if len(node_ids) != len(node_id_set):
            raise ValueError("orchestration graph node ids must be unique")

        edge_pairs: set[tuple[str, str]] = set()
        for edge in self.edges:
            if edge.from_node_id not in node_id_set:
                raise ValueError(
                    f"orchestration graph edge references unknown from_node_id: {edge.from_node_id}"
                )
            if edge.to_node_id not in node_id_set:
                raise ValueError(
                    f"orchestration graph edge references unknown to_node_id: {edge.to_node_id}"
                )
            edge_pair = (edge.from_node_id, edge.to_node_id)
            if edge_pair in edge_pairs:
                raise ValueError("orchestration graph edges must be unique")
            edge_pairs.add(edge_pair)

        if (
            self.final_response_node_id is not None
            and self.final_response_node_id not in node_id_set
        ):
            raise ValueError(
                "orchestration graph final_response_node_id must reference a node"
            )

        _ = self.topological_node_ids()
        return self

    def node_by_id(self, node_id: str) -> OrchestrationGraphNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"Unknown orchestration graph node: {node_id}")

    def upstream_node_ids(self, node_id: str) -> tuple[str, ...]:
        return tuple(
            edge.from_node_id for edge in self.edges if edge.to_node_id == node_id
        )

    def topological_node_ids(self) -> tuple[str, ...]:
        outgoing: dict[str, list[str]] = {node.node_id: [] for node in self.nodes}
        indegree: dict[str, int] = {node.node_id: 0 for node in self.nodes}
        for edge in self.edges:
            outgoing[edge.from_node_id].append(edge.to_node_id)
            indegree[edge.to_node_id] += 1

        ready = deque(
            node.node_id for node in self.nodes if indegree[node.node_id] == 0
        )
        ordered: list[str] = []
        while ready:
            node_id = ready.popleft()
            ordered.append(node_id)
            for downstream_node_id in outgoing[node_id]:
                indegree[downstream_node_id] -= 1
                if indegree[downstream_node_id] == 0:
                    ready.append(downstream_node_id)

        if len(ordered) != len(self.nodes):
            raise ValueError("orchestration graph must be acyclic")
        return tuple(ordered)


def build_orchestration_graph_prompt(graph: OrchestrationGraph) -> str:
    lines = [
        "## Orchestration Graph",
        (
            "The selected orchestration preset includes a DAG template. "
            + "Coordinator should respect node dependencies, use completed upstream node results as downstream context, and summarize the completed graph in the final response."
        ),
        "",
        "Nodes:",
    ]
    for node in graph.nodes:
        title_suffix = f" ({node.title})" if node.title else ""
        lines.append(f"- {node.node_id}{title_suffix}: role={node.role_id}")
        lines.append(f"  objective={node.objective}")
    if graph.edges:
        lines.append("")
        lines.append("Edges:")
        for edge in graph.edges:
            condition = f" when {edge.condition}" if edge.condition else ""
            lines.append(f"- {edge.from_node_id} -> {edge.to_node_id}{condition}")
    lines.append("")
    lines.append(f"Max parallel delegated tasks: {graph.max_parallel_tasks}")
    if graph.final_response_node_id:
        lines.append(f"Final response anchor node: {graph.final_response_node_id}")
    return "\n".join(lines)
