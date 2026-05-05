# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from relay_teams.agents.orchestration.a2a_bus import A2ABus
from relay_teams.agents.orchestration.a2a_bus_models import (
    A2aBusMessage,
    A2aSubscription,
)
from relay_teams.agents.orchestration.a2a_tool import (
    send_a2a_message,
    subscribe_a2a_topic,
)
from relay_teams.agents.orchestration.role_communication import (
    A2aMessage,
    RoleCommunicationExchange,
    RoleConversationMemoryScope,
    RoleStateTransition,
    build_a2a_bus_message,
    build_a2a_message_from_exchange,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_event_log() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def bus(mock_event_log: AsyncMock) -> A2ABus:
    return A2ABus(run_id="run-1", event_log=mock_event_log)


def _make_message(
    message_id: str = "msg-1",
    *,
    topic: str = "status_update",
    content: str = "test",
    target_role_id: str | None = None,
) -> A2aBusMessage:
    return A2aBusMessage(
        message_id=message_id,
        sender_role_id="explorer",
        sender_instance_id="inst-1",
        topic=topic,
        content=content,
        target_role_id=target_role_id,
    )


# ---------------------------------------------------------------------------
# AC-1, 2, 3: Publish broadcast / targeted / receive by topic
# ---------------------------------------------------------------------------


class TestA2aBusPublish:
    @pytest.mark.asyncio
    async def test_publish_broadcast(
        self, bus: A2ABus, mock_event_log: AsyncMock
    ) -> None:
        sub = A2aSubscription(
            role_id="designer",
            instance_id="inst-2",
            topic="status_update",
            receive_broadcast=True,
        )
        await bus.subscribe(sub)

        msg = _make_message(topic="status_update", content="broadcast test")
        await bus.publish(msg)

        # Check event log was called for publish and deliver
        assert mock_event_log.emit_async.call_count >= 2

        # Receive works
        received = await bus.receive("designer")
        assert len(received) == 1
        assert received[0].content == "broadcast test"

    @pytest.mark.asyncio
    async def test_publish_targeted(
        self, bus: A2ABus, mock_event_log: AsyncMock
    ) -> None:
        sub_a = A2aSubscription(
            role_id="designer",
            instance_id="inst-2",
            topic="status_update",
            receive_broadcast=True,
        )
        sub_b = A2aSubscription(
            role_id="crafter",
            instance_id="inst-3",
            topic="status_update",
            receive_broadcast=True,
        )
        await bus.subscribe(sub_a)
        await bus.subscribe(sub_b)

        msg = _make_message(
            topic="status_update",
            content="for designer only",
            target_role_id="designer",
        )
        await bus.publish(msg)

        # Designer receives
        received_designer = await bus.receive("designer")
        assert len(received_designer) == 1
        assert received_designer[0].content == "for designer only"

        # Crafter does NOT receive (targeted, not broadcast)
        received_crafter = await bus.receive("crafter")
        assert len(received_crafter) == 0

    @pytest.mark.asyncio
    async def test_receive_filters_by_topic(self, bus: A2ABus) -> None:
        sub = A2aSubscription(
            role_id="designer",
            instance_id="inst-2",
            topic="file_discovery",
            receive_broadcast=True,
        )
        await bus.subscribe(sub)

        sub2 = A2aSubscription(
            role_id="designer",
            instance_id="inst-2",
            topic="status_update",
            receive_broadcast=True,
        )
        await bus.subscribe(sub2)

        msg1 = _make_message("msg-1", topic="file_discovery", content="files found")
        msg2 = _make_message("msg-2", topic="status_update", content="status change")
        await bus.publish(msg1)
        await bus.publish(msg2)

        received = await bus.receive("designer", topic="file_discovery")
        assert len(received) == 1
        assert received[0].content == "files found"

    @pytest.mark.asyncio
    async def test_no_broadcast_subscriber_skipped(self, bus: A2ABus) -> None:
        sub = A2aSubscription(
            role_id="designer",
            instance_id="inst-2",
            topic="status_update",
            receive_broadcast=False,
        )
        await bus.subscribe(sub)

        msg = _make_message(topic="status_update", content="broadcast")
        await bus.publish(msg)

        received = await bus.receive("designer")
        assert len(received) == 0


# ---------------------------------------------------------------------------
# AC-4: History capped
# ---------------------------------------------------------------------------


class TestA2aBusHistory:
    @pytest.mark.asyncio
    async def test_history_capped(self, bus: A2ABus) -> None:
        bus._max_history = 5
        for i in range(10):
            msg = _make_message(f"msg-{i}", content=f"content-{i}")
            await bus.publish(msg)

        history = bus.get_history()
        assert len(history) == 5
        # Oldest should be evicted; we should see msg-5 through msg-9
        ids = [m.message_id for m in history]
        assert "msg-0" not in ids
        assert "msg-9" in ids

    def test_history_filters_by_topic(self, bus: A2ABus) -> None:
        # Manually append to history for sync test
        msg1 = _make_message("msg-1", topic="status_update", content="a")
        msg2 = _make_message("msg-2", topic="file_discovery", content="b")
        bus._message_history = [msg1, msg2]

        result = bus.get_history(topic="file_discovery")
        assert len(result) == 1
        assert result[0].message_id == "msg-2"

    def test_history_filters_by_role(self, bus: A2ABus) -> None:
        msg1 = A2aBusMessage(
            message_id="msg-1",
            sender_role_id="explorer",
            sender_instance_id="inst-1",
            topic="status_update",
            content="test",
            target_role_id="designer",
        )
        msg2 = A2aBusMessage(
            message_id="msg-2",
            sender_role_id="crafter",
            sender_instance_id="inst-2",
            topic="status_update",
            content="test",
        )
        bus._message_history = [msg1, msg2]

        result = bus.get_history(role_id="explorer")
        assert len(result) == 1
        assert result[0].message_id == "msg-1"

        result = bus.get_history(role_id="designer")
        assert len(result) == 1
        assert result[0].message_id == "msg-1"


# ---------------------------------------------------------------------------
# AC-5: Subscribe / unsubscribe
# ---------------------------------------------------------------------------


class TestA2aBusSubscribeUnsubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_and_unsubscribe(self, bus: A2ABus) -> None:
        sub = A2aSubscription(
            role_id="explorer",
            instance_id="inst-1",
            topic="file_discovery",
        )
        await bus.subscribe(sub)

        state = bus.snapshot()
        assert state.subscription_count == 1
        assert "file_discovery" in state.active_topics

        await bus.unsubscribe("explorer", "inst-1", "file_discovery")
        state2 = bus.snapshot()
        assert state2.subscription_count == 0

    def test_snapshot(self, bus: A2ABus) -> None:
        state = bus.snapshot()
        assert state.run_id == "run-1"
        assert state.message_count == 0
        assert state.subscription_count == 0


# ---------------------------------------------------------------------------
# AC-6: RoleCommunicationExchange -> A2aMessage
# ---------------------------------------------------------------------------


class TestA2aMessageFromExchange:
    def test_build_from_exchange(self) -> None:
        scope = RoleConversationMemoryScope(
            workspace_id="ws-1",
            role_id="designer",
            conversation_id="conv-1",
        )
        transition = RoleStateTransition(from_state="idle", to_state="working")
        exchange = RoleCommunicationExchange(
            sender_role_id="explorer",
            receiver_role_id="designer",
            memory_scope=scope,
            transition=transition,
            content="Found 5 files in src/",
        )

        a2a_msg = build_a2a_message_from_exchange(exchange, topic="file_discovery")
        assert a2a_msg.sender_role_id == "explorer"
        assert a2a_msg.receiver_role_id == "designer"
        assert a2a_msg.topic == "file_discovery"
        assert a2a_msg.content == "Found 5 files in src/"

    def test_build_bus_message(self) -> None:
        scope = RoleConversationMemoryScope(
            workspace_id="ws-1",
            role_id="designer",
            conversation_id="conv-1",
        )
        transition = RoleStateTransition(from_state="idle", to_state="working")
        a2a_msg = A2aMessage(
            sender_role_id="explorer",
            receiver_role_id="designer",
            memory_scope=scope,
            transition=transition,
            content="Test",
            topic="file_discovery",
        )
        bus_msg_raw = build_a2a_bus_message(
            a2a_msg,
            message_id="msg-1",
            sender_instance_id="inst-1",
        )
        assert isinstance(bus_msg_raw, A2aBusMessage)
        assert bus_msg_raw.message_id == "msg-1"
        assert bus_msg_raw.sender_instance_id == "inst-1"
        assert bus_msg_raw.target_role_id == "designer"


# ---------------------------------------------------------------------------
# AC-7: message validation
# ---------------------------------------------------------------------------


class TestMessageValidation:
    def test_empty_message_id_raises(self) -> None:
        with pytest.raises(ValueError):
            A2aBusMessage(
                message_id="",
                sender_role_id="explorer",
                sender_instance_id="inst-1",
                topic="status_update",
                content="x",
            )

    def test_empty_content_raises(self) -> None:
        with pytest.raises(ValueError):
            A2aBusMessage(
                message_id="msg-1",
                sender_role_id="explorer",
                sender_instance_id="inst-1",
                topic="status_update",
                content="",
            )


# ---------------------------------------------------------------------------
# AC-8: Tool send message
# ---------------------------------------------------------------------------


class TestA2aToolSendMessage:
    @pytest.mark.asyncio
    async def test_tool_send_message(self, bus: A2ABus) -> None:
        sub = A2aSubscription(
            role_id="designer",
            instance_id="inst-2",
            topic="status_update",
            receive_broadcast=True,
        )
        await bus.subscribe(sub)

        result = await send_a2a_message(
            topic="status_update",
            content="hello from tool",
            sender_role_id="explorer",
            sender_instance_id="inst-1",
            a2a_bus=bus,
        )
        assert result["published"] is True
        assert isinstance(result["message_id"], str)
        assert result["message_id"].startswith("a2a-")

    @pytest.mark.asyncio
    async def test_tool_subscribe(self, bus: A2ABus) -> None:
        result = await subscribe_a2a_topic(
            topic="file_discovery",
            sender_role_id="explorer",
            sender_instance_id="inst-1",
            a2a_bus=bus,
        )
        assert result["subscribed"] is True
        assert result["topic"] == "file_discovery"

        state = bus.snapshot()
        assert "file_discovery" in state.active_topics


# ---------------------------------------------------------------------------
# AC-9: role_communication models unchanged
# ---------------------------------------------------------------------------


class TestRoleCommunicationModelsUnchanged:
    def test_role_state_space_still_validates(self) -> None:
        from relay_teams.agents.orchestration.role_communication import (
            RoleStateSpace,
            RoleStateTransition,
        )

        space = RoleStateSpace(
            role_id="explorer",
            states=("idle", "working", "done"),
            initial_state="idle",
            terminal_states=("done",),
            transitions=(RoleStateTransition(from_state="idle", to_state="working"),),
        )
        assert space.allows_transition("idle", "working") is True
        assert space.allows_transition("idle", "done") is False

    def test_feedback_loop_still_works(self) -> None:
        from relay_teams.agents.orchestration.role_communication import (
            FeedbackLoopSpec,
            evaluate_feedback_loop,
        )

        spec = FeedbackLoopSpec(
            acceptance_criteria=("criterion-1",),
            verification_points=("vp-1",),
            max_iterations=3,
        )
        eval_result = evaluate_feedback_loop(
            spec,
            observed_signals=("criterion-1", "vp-1"),
            iteration=1,
        )
        assert eval_result.converged is True

    def test_validate_communication_still_works(self) -> None:
        from relay_teams.agents.orchestration.role_communication import (
            RoleCommunicationExchange,
            RoleConversationMemoryScope,
            RoleStateSpace,
            RoleStateTransition,
            validate_role_communication,
        )

        space = RoleStateSpace(
            role_id="designer",
            states=("idle", "working"),
            initial_state="idle",
            transitions=(RoleStateTransition(from_state="idle", to_state="working"),),
        )
        scope = RoleConversationMemoryScope(
            workspace_id="ws-1",
            role_id="designer",
            conversation_id="conv-1",
        )
        exchange = RoleCommunicationExchange(
            sender_role_id="explorer",
            receiver_role_id="designer",
            memory_scope=scope,
            transition=RoleStateTransition(from_state="idle", to_state="working"),
            content="Test",
        )
        result = validate_role_communication(space, exchange)
        assert result.valid is True
