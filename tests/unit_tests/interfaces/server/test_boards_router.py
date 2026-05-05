# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestBoardsRouter:
    def test_list_boards(self, client: TestClient) -> None:
        response = client.get("/api/boards")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_list_board_tasks(self, client: TestClient) -> None:
        response = client.get("/api/boards/test-board/tasks")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_sync_board(self, client: TestClient) -> None:
        response = client.post("/api/boards/test-board/sync")
        assert response.status_code == 200
        data = response.json()
        assert data["synced"] is True
        assert data["board_id"] == "test-board"

    def test_update_board_task_state_valid(self, client: TestClient) -> None:
        response = client.put(
            "/api/boards/test-board/tasks/t-1/state",
            json={"state": "completed"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] is True
        assert data["state"] == "completed"

    def test_update_board_task_state_invalid(self, client: TestClient) -> None:
        response = client.put(
            "/api/boards/test-board/tasks/t-1/state",
            json={"state": "not_a_real_state"},
        )
        assert response.status_code == 400

    def test_get_state_map(self, client: TestClient) -> None:
        response = client.get("/api/boards/state-map")
        assert response.status_code == 200
        data = response.json()
        assert "task_status_to_board" in data
        assert "board_state_to_task_status" in data
