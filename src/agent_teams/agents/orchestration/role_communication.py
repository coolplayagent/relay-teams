# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_teams.agents.instances.models import AgentRuntimeRecord, SubAgentInstance
from agent_teams.roles.models import RoleDefinition


class RoleStateTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_state: str = Field(min_length=1)
    to_state: str = Field(min_length=1)


class RoleStateSpace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    states: tuple[str, ...] = Field(min_length=1)
    initial_state: str = Field(min_length=1)
    terminal_states: tuple[str, ...] = ()
    transitions: tuple[RoleStateTransition, ...] = ()

    @model_validator(mode="after")
    def _validate_state_space(self) -> RoleStateSpace:
        state_set = set(self.states)
        if self.initial_state not in state_set:
            raise ValueError("initial_state must be included in states")
        missing_terminal_states = [
            state for state in self.terminal_states if state not in state_set
        ]
        if missing_terminal_states:
            raise ValueError("terminal_states must be included in states")
        for transition in self.transitions:
            if (
                transition.from_state not in state_set
                or transition.to_state not in state_set
            ):
                raise ValueError("transition states must be included in states")
        return self

    def allows_transition(self, from_state: str, to_state: str) -> bool:
        state_set = set(self.states)
        if from_state not in state_set or to_state not in state_set:
            return False
        if from_state == to_state:
            return True
        return any(
            transition.from_state == from_state and transition.to_state == to_state
            for transition in self.transitions
        )


class RoleWorkspaceMemoryScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)


class RoleConversationMemoryScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)


class RoleTaskMemoryScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)


class RoleCommunicationExchange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_role_id: str = Field(min_length=1)
    receiver_role_id: str = Field(min_length=1)
    memory_scope: RoleConversationMemoryScope
    transition: RoleStateTransition
    content: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_memory_scope(self) -> RoleCommunicationExchange:
        if self.memory_scope.role_id != self.receiver_role_id:
            raise ValueError(
                "memory_scope.role_id must match receiver_role_id to preserve role-scoped memory"
            )
        return self


class RoleAgentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)


class RoleInstanceExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    transition: RoleStateTransition


class FeedbackLoopSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    verification_points: tuple[str, ...] = Field(min_length=1)
    max_iterations: int = Field(ge=1, default=3)


class FeedbackLoopEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    converged: bool
    unmet_acceptance_criteria: tuple[str, ...]
    unmet_verification_points: tuple[str, ...]
    can_continue: bool
    iteration: int = Field(ge=1)


class RoleCommunicationValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    reason: str = Field(min_length=1)


def bind_role_to_agent_instance(
    role_definition: RoleDefinition,
    agent_instance: AgentRuntimeRecord | SubAgentInstance,
) -> RoleAgentBinding:
    if agent_instance.role_id != role_definition.role_id:
        raise ValueError("agent instance role_id does not match role definition")
    return RoleAgentBinding(
        role_id=role_definition.role_id,
        instance_id=agent_instance.instance_id,
        workspace_id=agent_instance.workspace_id,
        conversation_id=agent_instance.conversation_id,
    )


def build_role_workspace_memory_scope_from_binding(
    binding: RoleAgentBinding,
) -> RoleWorkspaceMemoryScope:
    return RoleWorkspaceMemoryScope(
        workspace_id=binding.workspace_id,
        role_id=binding.role_id,
    )


def build_memory_scope_from_binding(
    binding: RoleAgentBinding,
) -> RoleConversationMemoryScope:
    return RoleConversationMemoryScope(
        workspace_id=binding.workspace_id,
        role_id=binding.role_id,
        conversation_id=binding.conversation_id,
    )


def build_task_memory_scope_from_binding(
    binding: RoleAgentBinding,
    task_id: str,
) -> RoleTaskMemoryScope:
    return RoleTaskMemoryScope(
        workspace_id=binding.workspace_id,
        role_id=binding.role_id,
        conversation_id=binding.conversation_id,
        task_id=task_id,
    )


def execute_role_transition(
    role_state_space: RoleStateSpace,
    execution: RoleInstanceExecution,
) -> RoleStateTransition:
    if execution.role_id != role_state_space.role_id:
        raise ValueError("instance role_id does not match role state space")
    if not role_state_space.allows_transition(
        execution.transition.from_state,
        execution.transition.to_state,
    ):
        raise ValueError("instance transition is outside role state space boundary")
    return execution.transition


def validate_role_communication(
    receiver_state_space: RoleStateSpace,
    exchange: RoleCommunicationExchange,
) -> RoleCommunicationValidation:
    if exchange.receiver_role_id != receiver_state_space.role_id:
        return RoleCommunicationValidation(
            valid=False,
            reason="receiver_role_id does not match receiver state space",
        )
    if exchange.memory_scope.role_id != receiver_state_space.role_id:
        return RoleCommunicationValidation(
            valid=False,
            reason="memory scope role_id is not aligned with receiver role",
        )
    if not receiver_state_space.allows_transition(
        exchange.transition.from_state,
        exchange.transition.to_state,
    ):
        return RoleCommunicationValidation(
            valid=False,
            reason="encoded transition is outside receiver role state space",
        )
    return RoleCommunicationValidation(
        valid=True,
        reason="communication exchange is valid for receiver role state space",
    )


def validate_exchange_binding(
    receiver_binding: RoleAgentBinding,
    exchange: RoleCommunicationExchange,
) -> RoleCommunicationValidation:
    if exchange.receiver_role_id != receiver_binding.role_id:
        return RoleCommunicationValidation(
            valid=False,
            reason="receiver role does not match bound role context",
        )
    if exchange.memory_scope.workspace_id != receiver_binding.workspace_id:
        return RoleCommunicationValidation(
            valid=False,
            reason="memory scope workspace_id does not match receiver bound workspace",
        )
    if exchange.memory_scope.conversation_id != receiver_binding.conversation_id:
        return RoleCommunicationValidation(
            valid=False,
            reason="memory scope conversation_id does not match receiver bound conversation",
        )
    return RoleCommunicationValidation(
        valid=True,
        reason="communication exchange matches receiver role binding",
    )


def evaluate_feedback_loop(
    spec: FeedbackLoopSpec,
    observed_signals: tuple[str, ...],
    iteration: int,
) -> FeedbackLoopEvaluation:
    observed = {signal.strip() for signal in observed_signals if signal.strip()}
    unmet_acceptance_criteria = tuple(
        criterion for criterion in spec.acceptance_criteria if criterion not in observed
    )
    unmet_verification_points = tuple(
        point for point in spec.verification_points if point not in observed
    )
    converged = (
        len(unmet_acceptance_criteria) == 0 and len(unmet_verification_points) == 0
    )
    can_continue = not converged and iteration < spec.max_iterations
    return FeedbackLoopEvaluation(
        converged=converged,
        unmet_acceptance_criteria=unmet_acceptance_criteria,
        unmet_verification_points=unmet_verification_points,
        can_continue=can_continue,
        iteration=iteration,
    )


def evaluate_feedback_loop_recursively(
    spec: FeedbackLoopSpec,
    observed_signal_history: tuple[tuple[str, ...], ...],
) -> FeedbackLoopEvaluation:
    if len(observed_signal_history) == 0:
        return evaluate_feedback_loop(spec, observed_signals=(), iteration=1)

    evaluation: FeedbackLoopEvaluation | None = None
    bounded_history = observed_signal_history[: spec.max_iterations]
    for index, signals in enumerate(bounded_history, start=1):
        evaluation = evaluate_feedback_loop(
            spec, observed_signals=signals, iteration=index
        )
        if evaluation.converged:
            return evaluation

    if evaluation is None:
        return evaluate_feedback_loop(spec, observed_signals=(), iteration=1)
    return evaluation
