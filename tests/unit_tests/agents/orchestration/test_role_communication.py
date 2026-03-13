# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.models import AgentRuntimeRecord
from agent_teams.agents.orchestration import (
    FeedbackLoopSpec,
    RoleAgentBinding,
    RoleCommunicationExchange,
    RoleConversationMemoryScope,
    RoleInstanceExecution,
    RoleStateSpace,
    RoleStateTransition,
    bind_role_to_agent_instance,
    build_memory_scope_from_binding,
    build_role_workspace_memory_scope_from_binding,
    build_task_memory_scope_from_binding,
    evaluate_feedback_loop,
    evaluate_feedback_loop_recursively,
    execute_role_transition,
    validate_exchange_binding,
    validate_role_communication,
)
from agent_teams.roles.models import RoleDefinition


def test_role_state_space_rejects_unknown_transition_state() -> None:
    with pytest.raises(
        ValueError, match="transition states must be included in states"
    ):
        _ = RoleStateSpace(
            role_id="planner",
            states=("pending", "running", "done"),
            initial_state="pending",
            transitions=(
                RoleStateTransition(from_state="pending", to_state="running"),
                RoleStateTransition(from_state="running", to_state="review"),
            ),
        )


def test_role_state_space_allows_declared_transition() -> None:
    state_space = RoleStateSpace(
        role_id="planner",
        states=("pending", "running", "done"),
        initial_state="pending",
        terminal_states=("done",),
        transitions=(
            RoleStateTransition(from_state="pending", to_state="running"),
            RoleStateTransition(from_state="running", to_state="done"),
        ),
    )

    assert state_space.allows_transition("running", "done") is True
    assert state_space.allows_transition("pending", "done") is False


def test_bind_role_to_agent_instance_builds_role_binding() -> None:
    role = RoleDefinition(
        role_id="reviewer",
        name="reviewer",
        version="1",
        tools=(),
        system_prompt="review",
    )
    runtime = AgentRuntimeRecord(
        run_id="run-1",
        trace_id="trace-1",
        session_id="session-1",
        instance_id="instance-r1",
        role_id="reviewer",
        workspace_id="workspace-alpha",
        conversation_id="session-1:reviewer",
        status=InstanceStatus.IDLE,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )

    binding = bind_role_to_agent_instance(role, runtime)

    assert binding == RoleAgentBinding(
        role_id="reviewer",
        instance_id="instance-r1",
        workspace_id="workspace-alpha",
        conversation_id="session-1:reviewer",
    )


def test_bind_role_to_agent_instance_rejects_role_mismatch() -> None:
    role = RoleDefinition(
        role_id="planner",
        name="planner",
        version="1",
        tools=(),
        system_prompt="plan",
    )
    runtime = AgentRuntimeRecord(
        run_id="run-1",
        trace_id="trace-1",
        session_id="session-1",
        instance_id="instance-r1",
        role_id="reviewer",
        workspace_id="workspace-alpha",
        conversation_id="session-1:reviewer",
        status=InstanceStatus.IDLE,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )

    with pytest.raises(ValueError, match="agent instance role_id does not match"):
        _ = bind_role_to_agent_instance(role, runtime)


def test_exchange_rejects_mismatched_memory_scope_role() -> None:
    with pytest.raises(
        ValueError,
        match="memory_scope.role_id must match receiver_role_id",
    ):
        _ = RoleCommunicationExchange(
            sender_role_id="planner",
            receiver_role_id="reviewer",
            memory_scope=RoleConversationMemoryScope(
                workspace_id="workspace-alpha",
                role_id="planner",
                conversation_id="session-a:planner",
            ),
            transition=RoleStateTransition(from_state="running", to_state="done"),
            content="Checklist satisfied and ready for verification",
        )


def test_role_state_space_rejects_unknown_noop_transition() -> None:
    state_space = RoleStateSpace(
        role_id="planner",
        states=("pending", "running", "done"),
        initial_state="pending",
        transitions=(
            RoleStateTransition(from_state="pending", to_state="running"),
            RoleStateTransition(from_state="running", to_state="done"),
        ),
    )

    assert state_space.allows_transition("unknown", "unknown") is False


def test_execute_role_transition_rejects_unknown_noop_transition() -> None:
    state_space = RoleStateSpace(
        role_id="reviewer",
        states=("queued", "reviewing", "approved"),
        initial_state="queued",
        transitions=(
            RoleStateTransition(from_state="queued", to_state="reviewing"),
            RoleStateTransition(from_state="reviewing", to_state="approved"),
        ),
    )
    execution = RoleInstanceExecution(
        instance_id="instance-r1",
        role_id="reviewer",
        transition=RoleStateTransition(from_state="unknown", to_state="unknown"),
    )

    with pytest.raises(ValueError, match="outside role state space boundary"):
        _ = execute_role_transition(state_space, execution)


def test_validate_role_communication_rejects_unknown_noop_transition() -> None:
    receiver_space = RoleStateSpace(
        role_id="reviewer",
        states=("queued", "reviewing", "approved"),
        initial_state="queued",
        transitions=(
            RoleStateTransition(from_state="queued", to_state="reviewing"),
            RoleStateTransition(from_state="reviewing", to_state="approved"),
        ),
    )
    exchange = RoleCommunicationExchange(
        sender_role_id="planner",
        receiver_role_id="reviewer",
        memory_scope=RoleConversationMemoryScope(
            workspace_id="workspace-alpha",
            role_id="reviewer",
            conversation_id="session-a:reviewer",
        ),
        transition=RoleStateTransition(from_state="unknown", to_state="unknown"),
        content="state unchanged",
    )

    validation = validate_role_communication(receiver_space, exchange)

    assert validation.valid is False


def test_build_memory_scope_from_binding_returns_conversation_scope_identity() -> None:
    binding = RoleAgentBinding(
        role_id="reviewer",
        instance_id="instance-r1",
        workspace_id="workspace-alpha",
        conversation_id="session-a:reviewer",
    )

    memory_scope = build_memory_scope_from_binding(binding)

    assert memory_scope.workspace_id == "workspace-alpha"
    assert memory_scope.role_id == "reviewer"
    assert memory_scope.conversation_id == "session-a:reviewer"


def test_build_role_workspace_memory_scope_from_binding_returns_role_scope_identity() -> (
    None
):
    binding = RoleAgentBinding(
        role_id="reviewer",
        instance_id="instance-r1",
        workspace_id="workspace-alpha",
        conversation_id="session-a:reviewer",
    )

    memory_scope = build_role_workspace_memory_scope_from_binding(binding)

    assert memory_scope.workspace_id == "workspace-alpha"
    assert memory_scope.role_id == "reviewer"


def test_build_task_memory_scope_from_binding_returns_task_scope_identity() -> None:
    binding = RoleAgentBinding(
        role_id="reviewer",
        instance_id="instance-r1",
        workspace_id="workspace-alpha",
        conversation_id="session-a:reviewer",
    )

    memory_scope = build_task_memory_scope_from_binding(binding, task_id="task-7")

    assert memory_scope.workspace_id == "workspace-alpha"
    assert memory_scope.role_id == "reviewer"
    assert memory_scope.conversation_id == "session-a:reviewer"
    assert memory_scope.task_id == "task-7"


def test_execute_role_transition_enforces_role_boundary() -> None:
    state_space = RoleStateSpace(
        role_id="reviewer",
        states=("queued", "reviewing", "approved"),
        initial_state="queued",
        transitions=(
            RoleStateTransition(from_state="queued", to_state="reviewing"),
            RoleStateTransition(from_state="reviewing", to_state="approved"),
        ),
    )
    execution = RoleInstanceExecution(
        instance_id="instance-r1",
        role_id="reviewer",
        transition=RoleStateTransition(from_state="reviewing", to_state="approved"),
    )

    transition = execute_role_transition(state_space, execution)

    assert transition.from_state == "reviewing"
    assert transition.to_state == "approved"


def test_validate_role_communication_requires_receiver_space_match() -> None:
    receiver_space = RoleStateSpace(
        role_id="reviewer",
        states=("queued", "reviewing", "approved"),
        initial_state="queued",
        transitions=(
            RoleStateTransition(from_state="queued", to_state="reviewing"),
            RoleStateTransition(from_state="reviewing", to_state="approved"),
        ),
    )
    exchange = RoleCommunicationExchange(
        sender_role_id="planner",
        receiver_role_id="reviewer",
        memory_scope=RoleConversationMemoryScope(
            workspace_id="workspace-alpha",
            role_id="reviewer",
            conversation_id="session-a:reviewer",
        ),
        transition=RoleStateTransition(from_state="reviewing", to_state="approved"),
        content="Checklist satisfied and ready for verification",
    )

    validation = validate_role_communication(receiver_space, exchange)

    assert validation.valid is True


def test_validate_exchange_binding_requires_workspace_and_conversation_match() -> None:
    binding = RoleAgentBinding(
        role_id="reviewer",
        instance_id="instance-r1",
        workspace_id="workspace-alpha",
        conversation_id="session-a:reviewer",
    )
    exchange = RoleCommunicationExchange(
        sender_role_id="planner",
        receiver_role_id="reviewer",
        memory_scope=RoleConversationMemoryScope(
            workspace_id="workspace-beta",
            role_id="reviewer",
            conversation_id="session-a:reviewer",
        ),
        transition=RoleStateTransition(from_state="reviewing", to_state="approved"),
        content="handoff",
    )

    validation = validate_exchange_binding(binding, exchange)

    assert validation.valid is False


def test_evaluate_feedback_loop_requires_acceptance_and_verification_signals() -> None:
    spec = FeedbackLoopSpec(
        acceptance_criteria=("accepted", "delivered"),
        verification_points=("tests_passed",),
        max_iterations=2,
    )

    evaluation = evaluate_feedback_loop(
        spec,
        observed_signals=("accepted", "tests_passed"),
        iteration=1,
    )

    assert evaluation.converged is False
    assert evaluation.unmet_acceptance_criteria == ("delivered",)
    assert evaluation.unmet_verification_points == ()
    assert evaluation.can_continue is True
    assert evaluation.iteration == 1


def test_evaluate_feedback_loop_stops_when_max_iteration_reached() -> None:
    spec = FeedbackLoopSpec(
        acceptance_criteria=("accepted",),
        verification_points=("tests_passed",),
        max_iterations=2,
    )

    evaluation = evaluate_feedback_loop(
        spec,
        observed_signals=("accepted",),
        iteration=2,
    )

    assert evaluation.converged is False
    assert evaluation.unmet_verification_points == ("tests_passed",)
    assert evaluation.can_continue is False


def test_recursive_feedback_loop_stops_at_first_converged_iteration() -> None:
    spec = FeedbackLoopSpec(
        acceptance_criteria=("accepted",),
        verification_points=("tests_passed",),
        max_iterations=3,
    )

    evaluation = evaluate_feedback_loop_recursively(
        spec,
        observed_signal_history=(
            ("accepted",),
            ("accepted", "tests_passed"),
            ("accepted", "tests_passed", "extra"),
        ),
    )

    assert evaluation.converged is True
    assert evaluation.iteration == 2
