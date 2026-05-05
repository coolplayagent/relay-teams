# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay_teams.agents.orchestration.a2a_bus_models import (
    A2aBusMessage,
    A2aBusState,
    A2aSubscription,
    A2aTopic,
)


class TestA2aBusMessage:
    def test_minimal_message(self) -> None:
        msg = A2aBusMessage(
            message_id="msg-1",
            sender_role_id="explorer",
            sender_instance_id="inst-1",
            topic="status_update",
            content="Task completed",
        )
        assert msg.message_id == "msg-1"
        assert msg.published_at is not None

    def test_message_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            A2aBusMessage(
                message_id="",
                sender_role_id="explorer",
                sender_instance_id="inst-1",
                topic="status_update",
                content="",
            )

    def test_message_with_target(self) -> None:
        msg = A2aBusMessage(
            message_id="msg-1",
            sender_role_id="explorer",
            sender_instance_id="inst-1",
            topic="file_discovery",
            content="Found 5 files",
            target_role_id="designer",
        )
        assert msg.target_role_id == "designer"

    def test_message_serialization(self) -> None:
        msg = A2aBusMessage(
            message_id="msg-1",
            sender_role_id="explorer",
            sender_instance_id="inst-1",
            topic="status_update",
            content="Task completed",
        )
        data = msg.model_dump()
        assert data["message_id"] == "msg-1"
        assert data["topic"] == "status_update"


class TestA2aSubscription:
    def test_minimal_subscription(self) -> None:
        sub = A2aSubscription(
            role_id="explorer",
            instance_id="inst-1",
            topic="file_discovery",
        )
        assert sub.receive_broadcast is True
        assert sub.created_at is not None

    def test_subscription_no_broadcast(self) -> None:
        sub = A2aSubscription(
            role_id="explorer",
            instance_id="inst-1",
            topic="file_discovery",
            receive_broadcast=False,
        )
        assert sub.receive_broadcast is False


class TestA2aTopic:
    def test_all_topics_are_strings(self) -> None:
        for topic in tuple(A2aTopic):
            assert isinstance(topic.value, str)
            assert len(topic.value) > 0

    def test_file_discovery(self) -> None:
        assert A2aTopic.FILE_DISCOVERY == "file_discovery"

    def test_status_update(self) -> None:
        assert A2aTopic.STATUS_UPDATE == "status_update"


class TestA2aBusState:
    def test_snapshot_fields(self) -> None:
        state = A2aBusState(
            run_id="run-1",
            message_count=5,
            subscription_count=3,
            active_topics=("status_update", "file_discovery"),
        )
        assert state.run_id == "run-1"
        assert state.message_count == 5
        assert state.subscription_count == 3
        assert "status_update" in state.active_topics
