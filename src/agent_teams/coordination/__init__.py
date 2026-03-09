# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.coordination.coordination_agent import build_coordination_agent
from agent_teams.coordination.human_gate import GateAction, GateManager
from agent_teams.coordination.role_communication import (
    FeedbackLoopEvaluation,
    FeedbackLoopSpec,
    RoleAgentBinding,
    RoleCommunicationExchange,
    RoleCommunicationValidation,
    RoleConversationMemoryScope,
    RoleInstanceExecution,
    RoleStateSpace,
    RoleStateTransition,
    RoleTaskMemoryScope,
    RoleWorkspaceMemoryScope,
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

__all__ = [
    "FeedbackLoopEvaluation",
    "FeedbackLoopSpec",
    "GateAction",
    "GateManager",
    "RoleAgentBinding",
    "RoleCommunicationExchange",
    "RoleCommunicationValidation",
    "RoleConversationMemoryScope",
    "RoleInstanceExecution",
    "RoleStateSpace",
    "RoleStateTransition",
    "RoleTaskMemoryScope",
    "RoleWorkspaceMemoryScope",
    "bind_role_to_agent_instance",
    "build_coordination_agent",
    "build_memory_scope_from_binding",
    "build_role_workspace_memory_scope_from_binding",
    "build_task_memory_scope_from_binding",
    "evaluate_feedback_loop",
    "evaluate_feedback_loop_recursively",
    "execute_role_transition",
    "validate_exchange_binding",
    "validate_role_communication",
]
