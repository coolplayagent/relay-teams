# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.agents.orchestration.settings_config_manager import (
        OrchestrationSettingsConfigManager,
    )
    from relay_teams.agents.orchestration.settings_models import (
        OrchestrationPreset,
        OrchestrationSettings,
    )
    from relay_teams.agents.orchestration.settings_service import (
        OrchestrationSettingsService,
    )
    from relay_teams.agents.orchestration.human_gate import GateAction, GateManager
    from relay_teams.agents.orchestration.meta_agent import MetaAgent
    from relay_teams.agents.orchestration.role_communication import (
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
    "OrchestrationPreset",
    "OrchestrationSettings",
    "OrchestrationSettingsConfigManager",
    "OrchestrationSettingsService",
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
        "relay_teams.agents.orchestration.role_communication",
        "FeedbackLoopEvaluation",
    ),
    "FeedbackLoopSpec": (
        "relay_teams.agents.orchestration.role_communication",
        "FeedbackLoopSpec",
    ),
    "GateAction": ("relay_teams.agents.orchestration.human_gate", "GateAction"),
    "GateManager": ("relay_teams.agents.orchestration.human_gate", "GateManager"),
    "MetaAgent": ("relay_teams.agents.orchestration.meta_agent", "MetaAgent"),
    "OrchestrationPreset": (
        "relay_teams.agents.orchestration.settings_models",
        "OrchestrationPreset",
    ),
    "OrchestrationSettings": (
        "relay_teams.agents.orchestration.settings_models",
        "OrchestrationSettings",
    ),
    "OrchestrationSettingsConfigManager": (
        "relay_teams.agents.orchestration.settings_config_manager",
        "OrchestrationSettingsConfigManager",
    ),
    "OrchestrationSettingsService": (
        "relay_teams.agents.orchestration.settings_service",
        "OrchestrationSettingsService",
    ),
    "RoleAgentBinding": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleAgentBinding",
    ),
    "RoleCommunicationExchange": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleCommunicationExchange",
    ),
    "RoleCommunicationValidation": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleCommunicationValidation",
    ),
    "RoleConversationMemoryScope": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleConversationMemoryScope",
    ),
    "RoleInstanceExecution": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleInstanceExecution",
    ),
    "RoleStateSpace": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleStateSpace",
    ),
    "RoleStateTransition": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleStateTransition",
    ),
    "RoleTaskMemoryScope": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleTaskMemoryScope",
    ),
    "RoleWorkspaceMemoryScope": (
        "relay_teams.agents.orchestration.role_communication",
        "RoleWorkspaceMemoryScope",
    ),
    "bind_role_to_agent_instance": (
        "relay_teams.agents.orchestration.role_communication",
        "bind_role_to_agent_instance",
    ),
    "build_memory_scope_from_binding": (
        "relay_teams.agents.orchestration.role_communication",
        "build_memory_scope_from_binding",
    ),
    "build_role_workspace_memory_scope_from_binding": (
        "relay_teams.agents.orchestration.role_communication",
        "build_role_workspace_memory_scope_from_binding",
    ),
    "build_task_memory_scope_from_binding": (
        "relay_teams.agents.orchestration.role_communication",
        "build_task_memory_scope_from_binding",
    ),
    "evaluate_feedback_loop": (
        "relay_teams.agents.orchestration.role_communication",
        "evaluate_feedback_loop",
    ),
    "evaluate_feedback_loop_recursively": (
        "relay_teams.agents.orchestration.role_communication",
        "evaluate_feedback_loop_recursively",
    ),
    "execute_role_transition": (
        "relay_teams.agents.orchestration.role_communication",
        "execute_role_transition",
    ),
    "validate_exchange_binding": (
        "relay_teams.agents.orchestration.role_communication",
        "validate_exchange_binding",
    ),
    "validate_role_communication": (
        "relay_teams.agents.orchestration.role_communication",
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
