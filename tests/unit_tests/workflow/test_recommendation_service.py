# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.workflow.recommendation_service import WorkflowRecommendationService
from agent_teams.workflow.registry import WorkflowLoader


def test_workflow_recommendation_service_matches_delivery_intent() -> None:
    registry = WorkflowLoader().load_all(Path(".agent_teams/workflows"))

    decision = WorkflowRecommendationService(registry).recommend(
        "Build an API service with tests"
    )

    assert decision.recommendation is not None
    assert decision.recommendation.workflow_id == "sdd"
    assert "api" in decision.recommendation.matched_hints
    assert "service" in decision.recommendation.matched_hints
    assert "Intent matched workflow 'sdd'" in decision.recommendation.reason


def test_workflow_recommendation_service_defaults_when_objective_missing() -> None:
    registry = WorkflowLoader().load_all(Path(".agent_teams/workflows"))

    decision = WorkflowRecommendationService(registry).recommend("")

    assert decision.recommendation is not None
    assert decision.recommendation.workflow_id == "sdd"
    assert decision.recommendation.matched_hints == ()
    assert "No objective provided" in decision.recommendation.reason
