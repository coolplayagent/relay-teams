# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.workflow.registry import WorkflowLoader


def test_workflow_loader_loads_markdown_workflow() -> None:
    registry = WorkflowLoader().load_all(Path(".agent_teams/workflows"))

    workflow = registry.get("sdd")

    assert workflow.name == "Standard Delivery Workflow"
    assert workflow.is_default is True
    assert len(workflow.tasks) == 4
    assert workflow.tasks[1].depends_on == ("spec",)


def test_workflow_registry_recommends_default_for_delivery_intent() -> None:
    registry = WorkflowLoader().load_all(Path(".agent_teams/workflows"))

    recommended = registry.recommend("Build an API service with tests")

    assert recommended is not None
    assert recommended.workflow_id == "sdd"
