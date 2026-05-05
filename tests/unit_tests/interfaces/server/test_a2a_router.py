# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestA2aInternalRouter:
    def test_get_bus_state(self, client: TestClient) -> None:
        response = client.get("/api/a2a/runs/run-1/bus")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-1"
        assert data["message_count"] == 0

    def test_list_messages(self, client: TestClient) -> None:
        response = client.get("/api/a2a/runs/run-1/messages")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_list_messages_with_params(self, client: TestClient) -> None:
        response = client.get(
            "/api/a2a/runs/run-1/messages",
            params={"topic": "test", "role_id": "role-1"},
        )
        assert response.status_code == 200

    def test_list_subscriptions(self, client: TestClient) -> None:
        response = client.get("/api/a2a/runs/run-1/subscriptions")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_publish_message(self, client: TestClient) -> None:
        response = client.post(
            "/api/a2a/runs/run-1/messages",
            json={
                "sender_role_id": "role-1",
                "sender_instance_id": "inst-1",
                "topic": "test-topic",
                "content": "hello",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["published"] is True
        assert data["topic"] == "test-topic"
