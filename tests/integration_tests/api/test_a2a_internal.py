# -*- coding: utf-8 -*-
from __future__ import annotations


import pytest

from relay_teams.agent_runtimes.bus import A2ABus
from relay_teams.agent_runtimes.bus_models import (
    A2aBusMessage,
    A2aBusState,
    A2aSubscription,
)


class TestA2aBusLifecycle:
    """Integration: A2A bus lifecycle per run."""

    @pytest.mark.asyncio
    async def test_bus_state_reflects_run_id(self) -> None:
        """Bus created with a run_id; snapshot reflects it."""
        bus = A2ABus(run_id="run-test-001")
        state = bus.snapshot()
        assert state.run_id == "run-test-001"
        assert state.message_count == 0
        assert state.subscription_count == 0
        assert state.active_topics == ()

    @pytest.mark.asyncio
    async def test_bus_message_count_after_publish(self) -> None:
        """Publishing a message increments message_count in snapshot."""
        bus = A2ABus(run_id="run-test-002")
        msg = A2aBusMessage(
            message_id="msg-1",
            sender_role_id="explorer",
            sender_instance_id="inst-1",
            topic="file_discovery",
            content="Found 10 Python files",
        )
        await bus.publish(msg)
        state = bus.snapshot()
        assert state.message_count == 1

    @pytest.mark.asyncio
    async def test_bus_subscription_count_after_subscribe(self) -> None:
        """Subscribing increments subscription_count in snapshot."""
        bus = A2ABus(run_id="run-test-003")
        sub = A2aSubscription(
            role_id="designer",
            instance_id="inst-2",
            topic="file_discovery",
        )
        await bus.subscribe(sub)
        state = bus.snapshot()
        assert state.subscription_count == 1
        assert state.active_topics == ("file_discovery",)


class TestA2aMessageEndToEnd:
    """Integration: publish -> deliver -> receive full chain."""

    @pytest.mark.asyncio
    async def test_broadcast_to_subscriber(self) -> None:
        """Broadcast message reaches all subscribers of the topic."""
        bus = A2ABus(run_id="run-e2e-001")

        # Subscribe designer to file_discovery
        await bus.subscribe(
            A2aSubscription(
                role_id="designer",
                instance_id="inst-d",
                topic="file_discovery",
            )
        )

        # Explorer publishes a message (broadcast, no target_role_id)
        msg = A2aBusMessage(
            message_id="msg-bc-1",
            sender_role_id="explorer",
            sender_instance_id="inst-e",
            topic="file_discovery",
            content="Discovered module layout",
        )
        await bus.publish(msg)

        # Designer receives the message
        received = await bus.receive("designer")
        assert len(received) == 1
        assert received[0].message_id == "msg-bc-1"
        assert received[0].topic == "file_discovery"

        # After receive, queue is empty
        received_again = await bus.receive("designer")
        assert len(received_again) == 0

    @pytest.mark.asyncio
    async def test_targeted_message_only_reaches_target(self) -> None:
        """Targeted message only reaches the specified role."""
        bus = A2ABus(run_id="run-e2e-002")

        # Subscribe two roles to the same topic
        await bus.subscribe(
            A2aSubscription(
                role_id="designer",
                instance_id="inst-d",
                topic="status_update",
            )
        )
        await bus.subscribe(
            A2aSubscription(
                role_id="crafter",
                instance_id="inst-c",
                topic="status_update",
            )
        )

        # Send targeted message to designer only
        msg = A2aBusMessage(
            message_id="msg-tgt-1",
            sender_role_id="coordinator",
            sender_instance_id="inst-coord",
            topic="status_update",
            content="Design review needed",
            target_role_id="designer",
        )
        await bus.publish(msg)

        # Designer gets it
        designer_msgs = await bus.receive("designer")
        assert len(designer_msgs) == 1

        # Crafter does NOT get it
        crafter_msgs = await bus.receive("crafter")
        assert len(crafter_msgs) == 0

    @pytest.mark.asyncio
    async def test_history_survives_receive(self) -> None:
        """Message history remains after receive() empties the queue."""
        bus = A2ABus(run_id="run-e2e-003")

        await bus.subscribe(
            A2aSubscription(
                role_id="gater",
                instance_id="inst-g",
                topic="artifact_ready",
            )
        )

        msg = A2aBusMessage(
            message_id="msg-hist-1",
            sender_role_id="crafter",
            sender_instance_id="inst-c",
            topic="artifact_ready",
            content="Implementation complete",
        )
        await bus.publish(msg)

        # Receive dequeues
        await bus.receive("gater")

        # History still has it
        history = bus.get_history()
        assert len(history) == 1
        assert history[0].message_id == "msg-hist-1"

    @pytest.mark.asyncio
    async def test_receive_filters_by_topic(self) -> None:
        """Receive with topic filter returns only matching messages."""
        bus = A2ABus(run_id="run-e2e-004")

        await bus.subscribe(
            A2aSubscription(
                role_id="coordinator",
                instance_id="inst-coord",
                topic="file_discovery",
            )
        )
        await bus.subscribe(
            A2aSubscription(
                role_id="coordinator",
                instance_id="inst-coord",
                topic="status_update",
            )
        )

        await bus.publish(
            A2aBusMessage(
                message_id="msg-fd-1",
                sender_role_id="explorer",
                sender_instance_id="inst-e",
                topic="file_discovery",
                content="Files found",
            )
        )
        await bus.publish(
            A2aBusMessage(
                message_id="msg-su-1",
                sender_role_id="crafter",
                sender_instance_id="inst-c",
                topic="status_update",
                content="Done",
            )
        )

        # Filter by topic
        fd_msgs = await bus.receive("coordinator", topic="file_discovery")
        assert len(fd_msgs) == 1
        assert fd_msgs[0].message_id == "msg-fd-1"

        su_msgs = await bus.receive("coordinator", topic="status_update")
        assert len(su_msgs) == 1
        assert su_msgs[0].message_id == "msg-su-1"


class TestA2aApiEndpoints:
    """Integration: A2A REST API response models."""

    def test_bus_state_response_serialization(self) -> None:
        """A2aBusState serializes correctly for API response."""
        state = A2aBusState(
            run_id="run-api-001",
            message_count=5,
            subscription_count=3,
            active_topics=("file_discovery", "status_update"),
        )
        data = state.model_dump(mode="json")
        assert data["run_id"] == "run-api-001"
        assert data["message_count"] == 5
        assert data["subscription_count"] == 3
        assert "file_discovery" in data["active_topics"]

    def test_a2a_bus_message_response_serialization(self) -> None:
        """A2aBusMessage serializes correctly for API response."""
        msg = A2aBusMessage(
            message_id="msg-api-1",
            sender_role_id="explorer",
            sender_instance_id="inst-e",
            topic="file_discovery",
            content="Found files",
            target_role_id="designer",
        )
        data = msg.model_dump(mode="json")
        assert data["message_id"] == "msg-api-1"
        assert data["target_role_id"] == "designer"

    def test_a2a_subscription_response_serialization(self) -> None:
        """A2aSubscription serializes correctly for API response."""
        sub = A2aSubscription(
            role_id="designer",
            instance_id="inst-d",
            topic="file_discovery",
        )
        data = sub.model_dump(mode="json")
        assert data["role_id"] == "designer"
        assert data["topic"] == "file_discovery"
        assert data["receive_broadcast"] is True
