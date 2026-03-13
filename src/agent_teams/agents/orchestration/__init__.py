# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.agents.orchestration.human_gate import GateAction, GateManager
    from agent_teams.agents.orchestration.meta_agent import MetaAgent
    from agent_teams.agents.orchestration.role_communication import (
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
    "MetaAgent",
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
    "build_memory_scope_from_binding",
    "build_role_workspace_memory_scope_from_binding",
    "build_task_memory_scope_from_binding",
    "evaluate_feedback_loop",
    "evaluate_feedback_loop_recursively",
    "execute_role_transition",
    "validate_exchange_binding",
    "validate_role_communication",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "FeedbackLoopEvaluation": (
        "agent_teams.agents.orchestration.role_communication",
        "FeedbackLoopEvaluation",
    ),
    "FeedbackLoopSpec": (
        "agent_teams.agents.orchestration.role_communication",
        "FeedbackLoopSpec",
    ),
    "GateAction": ("agent_teams.agents.orchestration.human_gate", "GateAction"),
    "GateManager": ("agent_teams.agents.orchestration.human_gate", "GateManager"),
    "MetaAgent": ("agent_teams.agents.orchestration.meta_agent", "MetaAgent"),
    "RoleAgentBinding": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleAgentBinding",
    ),
    "RoleCommunicationExchange": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleCommunicationExchange",
    ),
    "RoleCommunicationValidation": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleCommunicationValidation",
    ),
    "RoleConversationMemoryScope": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleConversationMemoryScope",
    ),
    "RoleInstanceExecution": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleInstanceExecution",
    ),
    "RoleStateSpace": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleStateSpace",
    ),
    "RoleStateTransition": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleStateTransition",
    ),
    "RoleTaskMemoryScope": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleTaskMemoryScope",
    ),
    "RoleWorkspaceMemoryScope": (
        "agent_teams.agents.orchestration.role_communication",
        "RoleWorkspaceMemoryScope",
    ),
    "bind_role_to_agent_instance": (
        "agent_teams.agents.orchestration.role_communication",
        "bind_role_to_agent_instance",
    ),
    "build_memory_scope_from_binding": (
        "agent_teams.agents.orchestration.role_communication",
        "build_memory_scope_from_binding",
    ),
    "build_role_workspace_memory_scope_from_binding": (
        "agent_teams.agents.orchestration.role_communication",
        "build_role_workspace_memory_scope_from_binding",
    ),
    "build_task_memory_scope_from_binding": (
        "agent_teams.agents.orchestration.role_communication",
        "build_task_memory_scope_from_binding",
    ),
    "evaluate_feedback_loop": (
        "agent_teams.agents.orchestration.role_communication",
        "evaluate_feedback_loop",
    ),
    "evaluate_feedback_loop_recursively": (
        "agent_teams.agents.orchestration.role_communication",
        "evaluate_feedback_loop_recursively",
    ),
    "execute_role_transition": (
        "agent_teams.agents.orchestration.role_communication",
        "execute_role_transition",
    ),
    "validate_exchange_binding": (
        "agent_teams.agents.orchestration.role_communication",
        "validate_exchange_binding",
    ),
    "validate_role_communication": (
        "agent_teams.agents.orchestration.role_communication",
        "validate_role_communication",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
