# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock

from relay_teams.interfaces.server.routers import auto_harness
from relay_teams.tools.generated_tools import (
    GeneratedToolRecord,
    GeneratedToolStatus,
    GeneratedToolTestCase,
)


def _make_record(
    tool_name: str = "my_tool",
    description: str = "A test tool",
    status: GeneratedToolStatus = GeneratedToolStatus.ENABLED,
    target_role_id: str = "crafter",
    created_by_role_id: str = "main_agent",
) -> GeneratedToolRecord:
    return GeneratedToolRecord(
        tool_name=tool_name,
        description=description,
        input_schema={"type": "object"},
        test_cases=(),
        code_hash="abc123",
        status=status,
        target_role_id=target_role_id,
        created_by_role_id=created_by_role_id,
    )


def _create_test_client(records: list[GeneratedToolRecord] | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(auto_harness.router, prefix="/api")

    mock_service = Mock()
    mock_service.list_records.return_value = (
        records
        if records is not None
        else [
            _make_record(),
            _make_record(tool_name="other_tool", status=GeneratedToolStatus.PENDING),
        ]
    )
    mock_service.load_record.return_value = _make_record()

    mock_container = Mock()
    mock_container.auto_harness_service = mock_service
    app.state.container = mock_container

    return TestClient(app)


def test_list_generated_tools_returns_summaries() -> None:
    client = _create_test_client()
    response = client.get("/api/auto-harness/tools")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["tool_name"] == "my_tool"
    assert data[0]["status"] == "enabled"
    assert data[1]["tool_name"] == "other_tool"


def test_list_generated_tools_empty() -> None:
    client = _create_test_client(records=[])
    response = client.get("/api/auto-harness/tools")

    assert response.status_code == 200
    assert response.json() == []


def test_get_generated_tool_returns_detail() -> None:
    client = _create_test_client()
    response = client.get("/api/auto-harness/tools/my_tool")

    assert response.status_code == 200
    data = response.json()
    assert data["tool_name"] == "my_tool"
    assert data["description"] == "A test tool"
    assert "input_schema" in data
    assert "test_cases" in data


def test_get_generated_tool_returns_404_for_unknown() -> None:
    app = FastAPI()
    app.include_router(auto_harness.router, prefix="/api")

    mock_service = Mock()
    mock_service.load_record.side_effect = KeyError("Generated tool not found: missing")
    mock_container = Mock()
    mock_container.auto_harness_service = mock_service
    app.state.container = mock_container

    client = TestClient(app)
    response = client.get("/api/auto-harness/tools/missing")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_record_to_summary_includes_version_and_tests() -> None:
    record = _make_record().model_copy(
        update={
            "version": 3,
            "test_cases": (GeneratedToolTestCase(input={"a": 1}, expected={"b": 2}),),
        },
    )
    mock_service = Mock()
    mock_service.list_records.return_value = [record]
    mock_container = Mock()
    mock_container.auto_harness_service = mock_service

    app = FastAPI()
    app.include_router(auto_harness.router, prefix="/api")
    app.state.container = mock_container
    client = TestClient(app)

    response = client.get("/api/auto-harness/tools")
    data = response.json()
    assert data[0]["version"] == 3
    assert data[0]["test_count"] == 1


def test_record_to_detail_includes_test_case_dicts() -> None:
    record = _make_record().model_copy(
        update={
            "version": 2,
            "test_cases": (GeneratedToolTestCase(input={"x": 1}, expected={"y": 2}),),
        },
    )

    mock_service = Mock()
    mock_service.load_record.return_value = record
    mock_container = Mock()
    mock_container.auto_harness_service = mock_service

    app = FastAPI()
    app.include_router(auto_harness.router, prefix="/api")
    app.state.container = mock_container
    client = TestClient(app)

    response = client.get("/api/auto-harness/tools/my_tool")
    data = response.json()
    assert data["version"] == 2
    assert len(data["test_cases"]) == 1
    assert data["test_cases"][0]["input"] == {"x": 1}
