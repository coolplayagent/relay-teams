# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationRequest,
    LLMEvaluationResult,
    LLMEvaluationScore,
)
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskRecord,
    TaskSpec,
    VerificationPlan,
)
from relay_teams.interfaces.server.deps import get_llm_evaluator, get_task_service
from relay_teams.interfaces.server.routers import tasks


def _make_task_record(
    task_id: str = "t1",
    spec: TaskSpec | None = None,
) -> TaskRecord:
    envelope = TaskEnvelope(
        task_id=task_id,
        session_id="s1",
        trace_id="tr1",
        objective="test objective",
        spec=spec,
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    return TaskRecord(envelope=envelope)


def _make_result(score: float = 4.0) -> LLMEvaluationResult:
    return LLMEvaluationResult(
        scores=[LLMEvaluationScore(dimension="clarity", score=4)],
        overall_score=score,
        summary="ok",
        recommendations=[],
    )


def _create_app() -> tuple[FastAPI, Mock, Mock]:
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")

    mock_service = Mock()
    mock_evaluator = Mock()

    app.dependency_overrides[get_task_service] = lambda: mock_service
    app.dependency_overrides[get_llm_evaluator] = lambda: mock_evaluator

    return app, mock_service, mock_evaluator


def test_evaluate_task_spec_success() -> None:
    app, mock_service, mock_evaluator = _create_app()

    spec = TaskSpec(
        summary="Test summary",
        requirements=("req1",),
        constraints=("c1",),
        acceptance_criteria=("ac1",),
        evidence_expectations=("ev1",),
    )
    record = _make_task_record(spec=spec)
    mock_service.get_task_async = AsyncMock(return_value=record)
    mock_evaluator.evaluate_spec_quality = AsyncMock(return_value=_make_result())

    client = TestClient(app)
    response = client.post("/api/tasks/t1/evaluate-spec", json={"task_result": "done"})

    assert response.status_code == 200
    data = response.json()
    assert data["overall_score"] == 4.0


def test_evaluate_task_spec_missing_task() -> None:
    app, mock_service, mock_evaluator = _create_app()
    mock_service.get_task_async = AsyncMock(side_effect=KeyError("not found"))

    client = TestClient(app)
    response = client.post(
        "/api/tasks/missing/evaluate-spec", json={"task_result": "done"}
    )

    assert response.status_code == 404


def test_evaluate_task_spec_null_spec_uses_default() -> None:
    app, mock_service, mock_evaluator = _create_app()

    record = _make_task_record(spec=None)
    mock_service.get_task_async = AsyncMock(return_value=record)
    mock_evaluator.evaluate_spec_quality = AsyncMock(return_value=_make_result(3.0))

    client = TestClient(app)
    response = client.post("/api/tasks/t1/evaluate-spec", json={"task_result": "done"})

    assert response.status_code == 200
    call_args = mock_evaluator.evaluate_spec_quality.call_args[0][0]
    assert isinstance(call_args, LLMEvaluationRequest)
    assert call_args.spec_summary == ""
